"""Versioned shared Live Board order & custom workflow lanes in SQLite."""
from __future__ import annotations
import json
import re
from backend.core.app_time import utc_now
from backend.storage import db

KEY = "workspace_manual_order_v1"
CUSTOM_LANES_KEY = "workspace_custom_lanes_v1"
LANE_LABELS_KEY = "workspace_lane_labels_v1"
LANE_COLORS_KEY = "workspace_lane_colors_v1"
ISSUE_CATEGORIES_KEY = "workspace_issue_categories_v1"
BUILTIN_STAGES = ("jbs_plant", "meijer", "dealer_shop", "monitoring", "to_yard", "at_yard", "test")
SPECIAL_LANES = ("unassigned", "done")
AGENT_LANE_RE = re.compile(r"^agent_workflow:agent:[0-9]{1,20}$")
VALID_LANE_LABEL_KEY_RE = re.compile(r"^(?:agent:[0-9]{1,20}|legacy:.{1,48}|[a-z0-9_]{1,40})$")

DEFAULT_ISSUE_CATEGORIES = (
    "Windshield & Glass",
    "Tires & Wheel End",
    "Brakes & Air System",
    "Coolant & Cooling",
    "Engine & Aftertreatment (DEF/DPF)",
    "Electrical & Lights",
    "Reefer / Thermo King",
    "Transmission & Drivetrain",
    "Suspension & Steering",
    "Doors, Body & Liftgate",
    "PM & Oil Service",
    "DOT & Inspection",
    "Other / General",
)


class OrderConflict(Exception):
    """The client has a stale board-order version."""


def _sanitize_custom_lanes(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    seen = set(BUILTIN_STAGES)
    for item in raw:
        if not isinstance(item, dict):
            continue
        lid = re.sub(r"[^a-z0-9_]", "_", str(item.get("id") or "").strip().lower()).strip("_")[:40]
        name = " ".join(str(item.get("name") or "").split()).strip()[:40]
        if not lid or not name or lid in seen:
            continue
        seen.add(lid)
        color = str(item.get("color") or "#7c3aed").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#7c3aed"
        out.append({
            "id": lid,
            "name": name,
            "color": color,
            "created_by": str(item.get("created_by") or "")[:64],
            "created_at": str(item.get("created_at") or "")[:40],
        })
        if len(out) >= 25:
            break
    return out


def read_custom_lanes(data_dir=None) -> list[dict]:
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
    if not row:
        return []
    try:
        return _sanitize_custom_lanes(json.loads(row["data_json"]))
    except (ValueError, TypeError):
        return []


def allowed_stages(data_dir=None) -> set[str]:
    return {"", *BUILTIN_STAGES, *(x["id"] for x in read_custom_lanes(data_dir))}


def add_custom_lane(name: str, actor_id, color: str = "#7c3aed", data_dir=None) -> dict:
    clean_name = " ".join(str(name or "").split()).strip()[:40]
    if not clean_name:
        raise ValueError("Enter a lane name.")
    slug = re.sub(r"[^a-z0-9]+", "_", clean_name.lower()).strip("_")[:28] or "lane"
    safe_color = str(color or "#7c3aed").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", safe_color):
        safe_color = "#7c3aed"
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
        version = int(row["version"]) if row else 0
        existing = _sanitize_custom_lanes(json.loads(row["data_json"])) if row else []
        if len(existing) >= 25:
            raise ValueError("Custom lane limit reached (25). Delete an unused lane first.")
        used = {"", *BUILTIN_STAGES, *(x["id"] for x in existing)}
        base = f"custom_{slug}"[:36]
        candidate = base
        idx = 2
        while candidate in used:
            candidate = f"{base[:32]}_{idx}"
            idx += 1
        entry = {
            "id": candidate,
            "name": clean_name,
            "color": safe_color,
            "created_by": str(actor_id or ""),
            "created_at": utc_now().isoformat(timespec="seconds"),
        }
        existing.append(entry)
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (CUSTOM_LANES_KEY, version + 1, json.dumps(existing, ensure_ascii=False, separators=(",", ":")))
        )
        db.bump_version(conn, "cases_version")
    return {"lane": entry, "custom_lanes": existing}


def delete_custom_lane(lane_id: str, data_dir=None) -> list[dict]:
    clean_id = str(lane_id or "").strip()
    if clean_id in BUILTIN_STAGES or not clean_id:
        raise ValueError("Built-in workflow lanes cannot be deleted.")
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
        version = int(row["version"]) if row else 0
        existing = _sanitize_custom_lanes(json.loads(row["data_json"])) if row else []
        remaining = [x for x in existing if x["id"] != clean_id]
        if len(remaining) == len(existing):
            raise LookupError("Custom lane not found.")
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (CUSTOM_LANES_KEY, version + 1, json.dumps(remaining, ensure_ascii=False, separators=(",", ":")))
        )
        # Re-home any active or archived cases in this stage back to Assigned Agent ("")
        case_rows = conn.execute(
            "SELECT id, data_json FROM cases WHERE board_stage=?",
            (clean_id,),
        ).fetchall()
        for crow in case_rows:
            try:
                cdict = json.loads(crow["data_json"])
            except Exception:
                continue
            if isinstance(cdict, dict) and str(cdict.get("board_stage") or "") == clean_id:
                cdict["board_stage"] = ""
                conn.execute(
                    "UPDATE cases SET board_stage='', data_json=? WHERE id=?",
                    (json.dumps(cdict, ensure_ascii=False, separators=(",", ":")), crow["id"]),
                )
        ord_row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (KEY,)).fetchone()
        if ord_row:
            try:
                groups = json.loads(ord_row["data_json"])
            except Exception:
                groups = {}
            if isinstance(groups, dict) and f"agent_workflow:stage:{clean_id}" in groups:
                groups.pop(f"agent_workflow:stage:{clean_id}", None)
                conn.execute(
                    "UPDATE kv_state SET version=?, data_json=? WHERE key=?",
                    (int(ord_row["version"]) + 1, json.dumps(groups, separators=(",", ":")), KEY),
                )
        db.bump_version(conn, "cases_version")
    return remaining


def _is_valid_lane_label_key(key: str) -> bool:
    if not key:
        return False
    if key in BUILTIN_STAGES or key in SPECIAL_LANES:
        return True
    return bool(VALID_LANE_LABEL_KEY_RE.fullmatch(key))


def read_lane_labels(data_dir=None) -> dict[str, str]:
    """Return custom display titles for workflow, agent, unassigned, and done lanes."""
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT data_json FROM kv_state WHERE key=?", (LANE_LABELS_KEY,)).fetchone()
    if not row:
        return {}
    try:
        parsed = json.loads(row["data_json"])
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(k): " ".join(str(v or "").split()).strip()[:40]
        for k, v in parsed.items()
        if _is_valid_lane_label_key(str(k)) and " ".join(str(v or "").split()).strip()
    }


def read_lane_colors(data_dir=None) -> dict[str, str]:
    """Return custom accent colors for built-in workflow, agent, unassigned, and done lanes."""
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT data_json FROM kv_state WHERE key=?", (LANE_COLORS_KEY,)).fetchone()
    if not row:
        return {}
    try:
        parsed = json.loads(row["data_json"])
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(k): str(v).strip()
        for k, v in parsed.items()
        if _is_valid_lane_label_key(str(k)) and re.fullmatch(r"#[0-9a-fA-F]{6}", str(v or "").strip())
    }


def rename_lane(lane_id: str, new_name: str, *, color: str | None = None, reset: bool = False, data_dir=None) -> dict:
    """Rename (and optionally recolor) any custom, built-in workflow, agent, unassigned, or done lane."""
    raw_key = str(lane_id or "").strip()
    clean_key = raw_key[len("stage:"):] if raw_key.startswith("stage:") else raw_key
    clean_name = " ".join(str(new_name or "").split()).strip()[:40]
    safe_color = str(color or "").strip() if color is not None else ""
    if safe_color and not re.fullmatch(r"#[0-9a-fA-F]{6}", safe_color):
        safe_color = ""
    if not reset and not clean_name:
        raise ValueError("Enter a lane name.")

    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (CUSTOM_LANES_KEY,)).fetchone()
        custom_ver = int(row["version"]) if row else 0
        custom_lanes = _sanitize_custom_lanes(json.loads(row["data_json"])) if row else []
        custom_hit = next((x for x in custom_lanes if x["id"] == clean_key), None)

        if custom_hit:
            if reset or not clean_name:
                raise ValueError("Enter a new name for this custom lane.")
            custom_hit["name"] = clean_name
            if safe_color:
                custom_hit["color"] = safe_color
            conn.execute(
                "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
                (CUSTOM_LANES_KEY, custom_ver + 1, json.dumps(custom_lanes, ensure_ascii=False, separators=(",", ":")))
            )
            labels = read_lane_labels(data_dir)
            colors = read_lane_colors(data_dir)
            db.bump_version(conn, "cases_version")
            return {"lane_id": raw_key, "name": clean_name, "color": custom_hit.get("color", ""), "custom_lanes": custom_lanes, "lane_labels": labels, "lane_colors": colors}

        if not _is_valid_lane_label_key(clean_key):
            raise LookupError("Lane not found or cannot be renamed.")

        lbl_row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (LANE_LABELS_KEY,)).fetchone()
        lbl_ver = int(lbl_row["version"]) if lbl_row else 0
        try:
            labels = json.loads(lbl_row["data_json"]) if lbl_row else {}
        except (ValueError, TypeError):
            labels = {}
        if not isinstance(labels, dict):
            labels = {}
        if reset:
            labels.pop(clean_key, None)
        else:
            labels[clean_key] = clean_name
        clean_labels = {
            str(k): " ".join(str(v or "").split()).strip()[:40]
            for k, v in labels.items()
            if _is_valid_lane_label_key(str(k)) and " ".join(str(v or "").split()).strip()
        }
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (LANE_LABELS_KEY, lbl_ver + 1, json.dumps(clean_labels, ensure_ascii=False, separators=(",", ":")))
        )

        col_row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (LANE_COLORS_KEY,)).fetchone()
        col_ver = int(col_row["version"]) if col_row else 0
        try:
            colors = json.loads(col_row["data_json"]) if col_row else {}
        except (ValueError, TypeError):
            colors = {}
        if not isinstance(colors, dict):
            colors = {}
        if reset or (color is not None and not safe_color):
            colors.pop(clean_key, None)
        elif safe_color:
            colors[clean_key] = safe_color
        clean_colors = {
            str(k): str(v).strip()
            for k, v in colors.items()
            if _is_valid_lane_label_key(str(k)) and re.fullmatch(r"#[0-9a-fA-F]{6}", str(v or "").strip())
        }
        if color is not None or reset:
            conn.execute(
                "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
                (LANE_COLORS_KEY, col_ver + 1, json.dumps(clean_colors, ensure_ascii=False, separators=(",", ":")))
            )

        db.bump_version(conn, "cases_version")
    return {
        "lane_id": raw_key,
        "name": clean_name if not reset else "",
        "color": clean_colors.get(clean_key, ""),
        "custom_lanes": custom_lanes,
        "lane_labels": clean_labels,
        "lane_colors": clean_colors,
    }


def _sanitize_issue_categories(raw) -> list[str]:
    if not isinstance(raw, list):
        return list(DEFAULT_ISSUE_CATEGORIES)
    out = []
    seen = set()
    for item in raw:
        name = " ".join(str(item or "").split()).strip()[:64]
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= 40:
            break
    return out if out else list(DEFAULT_ISSUE_CATEGORIES)


def read_issue_categories(data_dir=None) -> list[str]:
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT data_json FROM kv_state WHERE key=?", (ISSUE_CATEGORIES_KEY,)).fetchone()
    if not row:
        return list(DEFAULT_ISSUE_CATEGORIES)
    try:
        return _sanitize_issue_categories(json.loads(row["data_json"]))
    except (ValueError, TypeError):
        return list(DEFAULT_ISSUE_CATEGORIES)


def save_issue_categories(categories: list[str], data_dir=None) -> list[str]:
    cleaned = _sanitize_issue_categories(categories)
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version FROM kv_state WHERE key=?", (ISSUE_CATEGORIES_KEY,)).fetchone()
        ver = int(row["version"]) if row else 0
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (ISSUE_CATEGORIES_KEY, ver + 1, json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")))
        )
        db.bump_version(conn, "cases_version")
    return cleaned


_CATEGORY_KEYWORD_RULES = (
    ("Windshield & Glass", ("windshield", "windscreen", "wiper", "glass", "mirror", "defroster", "rock chip", "cracked glass")),
    ("Tires & Wheel End", ("tire", "tyre", "flat", "blowout", "puncture", "rim", "wheel seal", "hub", "hubcap", "lug nut", "mudflap", "steer tire", "drive tire", "tread")),
    ("Brakes & Air System", ("brake", "air leak", "air bag", "air line", "chamber", "slack adjuster", "abs", "gladhand", "compressor", "governor", "drum", "rotor", "caliper", "bendix", "parking valve")),
    ("Coolant & Cooling", ("coolant", "radiator", "water pump", "thermostat", "overheat", "antifreeze", "hose clamp", "fan clutch", "surge tank", "reservoir")),
    ("Reefer / Thermo King", ("reefer", "thermo king", "thermoking", "carrier unit", "setpoint", "freon", "refriger", "evaporator", "chute", "return air", "temp alarm", "alarm code")),
    ("Engine & Aftertreatment (DEF/DPF)", ("engine", "check engine", "cel", "def", "dpf", "regen", "scr", "nox", "injector", "turbo", "oil leak", "fuel pump", "fuel filter", "fuel leak", "derate", "egr", "spn", "fmi", "exhaust")),
    ("Electrical & Lights", ("light", "lamp", "headlight", "marker light", "tail light", "blinker", "turn signal", "alternator", "battery", "starter", "wiring", "fuse", "harness", "pigtail", "7-way", "inverter")),
    ("Transmission & Drivetrain", ("transmission", "clutch", "gearbox", "shifting", "driveshaft", "u-joint", "differential", "rear end", "axle")),
    ("Suspension & Steering", ("suspension", "shock", "leaf spring", "kingpin", "tie rod", "alignment", "steering", "power steering", "air spring", "leveling valve")),
    ("Doors, Body & Liftgate", ("door", "hinge", "latch", "roll door", "swing door", "liftgate", "bumper", "fender", "roof", "floor", "landing gear", "icc bar", "fairing", "hood", "fifth wheel")),
    ("PM & Oil Service", ("pm service", "oil change", "preventive", "lube", "filter service", "grease", "a-service", "b-service")),
    ("DOT & Inspection", ("dot inspection", "annual inspection", "inspection sticker", "registration", "permit", "ifta", "cab card", "dot violation")),
)


def infer_issue_category(case_or_text, categories: list[str] | None = None) -> str:
    """AI/heuristic tagger that classifies a maintenance case into one of the configured categories."""
    cats = list(categories) if categories else list(DEFAULT_ISSUE_CATEGORIES)
    if isinstance(case_or_text, dict):
        parts = [
            case_or_text.get("issue_text"),
            case_or_text.get("description"),
            case_or_text.get("full_description"),
            case_or_text.get("comments"),
            case_or_text.get("notes"),
            " ".join(
                str(n.get("text") or "")
                for n in (case_or_text.get("workspace_notes") or [])
                if isinstance(n, dict) and not n.get("deleted_at")
            ),
        ]
        vtype = str(case_or_text.get("vehicle_type") or "").lower()
        hay = " ".join(str(p or "") for p in parts if p).lower()
    else:
        hay = str(case_or_text or "").lower()
        vtype = ""

    if not hay.strip():
        return ""

    # 1. Exact or token match against any custom manager-configured categories first
    cat_by_low = {c.lower(): c for c in cats}
    for c in cats:
        clow = c.lower()
        if clow in ("other / general", "other", "general"):
            continue
        if clow in hay:
            return c
        tokens = [t for t in re.split(r"[^a-z0-9]+", clow) if len(t) >= 4 and t not in ("system", "service", "general", "other", "aftertreatment")]
        if tokens and any(re.search(r"\b" + re.escape(tok), hay) for tok in tokens):
            # Also check built-in synonym rules below before committing if a stronger synonym matches
            pass

    # 2. Domain synonym scoring mapped onto the active category list
    best_cat = ""
    best_score = 0
    for canonical, keywords in _CATEGORY_KEYWORD_RULES:
        # Resolve canonical to a matching active category if present
        target_cat = cat_by_low.get(canonical.lower())
        if not target_cat:
            canon_prefix = canonical.split()[0].lower()
            target_cat = next((c for c in cats if canon_prefix in c.lower()), None)
        if not target_cat:
            continue
        score = 0
        for kw in keywords:
            if kw in hay:
                score += 2 if " " in kw or len(kw) >= 6 else 1
        if score > best_score:
            best_score = score
            best_cat = target_cat

    if best_cat:
        return best_cat

    # 3. Check custom category tokens
    for c in cats:
        clow = c.lower()
        if clow in ("other / general", "other", "general"):
            continue
        tokens = [t for t in re.split(r"[^a-z0-9]+", clow) if len(t) >= 3 and t not in ("and", "the", "for", "system", "service")]
        if tokens and any(re.search(r"\b" + re.escape(tok), hay) for tok in tokens):
            return c

    if vtype == "reefer":
        reefer_cat = next((c for c in cats if "reefer" in c.lower() or "thermo" in c.lower()), None)
        if reefer_cat:
            return reefer_cat

    fallback = next((c for c in cats if "other" in c.lower() or "general" in c.lower()), "")
    return fallback or (cats[-1] if cats else "Other / General")


def read_order(data_dir=None):
    with db.read_conn(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {"version": 0, "groups": {}}
    try:
        groups = json.loads(row["data_json"])
    except (ValueError, TypeError):
        groups = {}
    return {"version": int(row["version"]), "groups": groups if isinstance(groups, dict) else {}}


def _is_valid_group(group: str, data_dir=None) -> bool:
    if not isinstance(group, str):
        return False
    if AGENT_LANE_RE.fullmatch(group):
        return True
    if group.startswith("agent_workflow:stage:"):
        stage = group[len("agent_workflow:stage:"):]
        return bool(stage) and stage in allowed_stages(data_dir)
    return False


def save_order(group, ids, expected_version, actor_id, *, manager=False, data_dir=None):
    if not _is_valid_group(group, data_dir):
        raise ValueError("Invalid board column.")
    if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(i, str) or not (1 <= len(i) <= 100) for i in ids):
        raise ValueError("Choose up to 500 valid case IDs.")
    if len(ids) != len(set(ids)):
        raise ValueError("The card order has repeated case IDs.")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise ValueError("Invalid order version.")
    if not manager and group != f"agent_workflow:agent:{actor_id}":
        raise PermissionError("Only a lead can reorder other agents' or workflow columns.")
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version,data_json FROM kv_state WHERE key=?", (KEY,)).fetchone()
        version = int(row["version"]) if row else 0
        if version != expected_version:
            raise OrderConflict("Another agent changed the board order. Refresh and retry.")
        groups = json.loads(row["data_json"]) if row else {}
        if not isinstance(groups, dict):
            groups = {}
        if ids:
            rows = conn.execute(
                "SELECT id,agent_id,status,closed_at,data_json FROM cases WHERE id IN (" +
                ",".join("?" for _ in ids) + ")", ids
            ).fetchall()
            if len(rows) != len(ids):
                raise ValueError("A case was removed or is no longer available. Refresh the board.")
            required_stage = group[len("agent_workflow:stage:"):] if group.startswith("agent_workflow:stage:") else None
            required_agent = group[len("agent_workflow:agent:"):] if group.startswith("agent_workflow:agent:") else None
            for case in rows:
                try:
                    info = json.loads(case["data_json"])
                except (TypeError, ValueError):
                    raise ValueError("A case could not be validated. Refresh the board.")
                if case["status"] != "reported" or info.get("closed_at") or case["closed_at"]:
                    raise ValueError("The board contains a closed or inactive case. Refresh and retry.")
                stage = str(info.get("board_stage") or "")
                if required_stage and stage != required_stage:
                    raise ValueError("A case moved to another column. Refresh and retry.")
                if required_agent and (stage or str(case["agent_id"]) != required_agent):
                    raise ValueError("A case moved to another agent. Refresh and retry.")
        groups[group] = ids
        next_version = version + 1
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (KEY, next_version, json.dumps(groups, separators=(",", ":")))
        )
        db.bump_version(conn, "cases_version")
    return {"version": next_version, "groups": groups}


def reset_order(data_dir=None) -> dict:
    """Clear manual card ordering across all lanes for Managers and Developers."""
    with db.write_tx(data_dir) as conn:
        row = conn.execute("SELECT version FROM kv_state WHERE key=?", (KEY,)).fetchone()
        next_version = (int(row["version"]) if row else 0) + 1
        conn.execute(
            "INSERT INTO kv_state(key,version,data_json) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version,data_json=excluded.data_json",
            (KEY, next_version, "{}"),
        )
        db.bump_version(conn, "cases_version")
    return {"version": next_version, "groups": {}}
