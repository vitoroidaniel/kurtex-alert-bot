"""Kurtex Live Board (Case Workspace) API & Realtime WebSocket routes."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import urllib.parse
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock

from flask import g, jsonify, request, send_file, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from backend.ai.live_shop_search import search_live_repair_shops
from backend.core.app_time import chicago_date_str, chicago_now, chicago_timestamp, utc_now
from backend.core.case_snapshot import normalize as normalize_case
from backend.storage import case_store, db as kurtex_db, shift_store
from backend.storage.board_order import (
    OrderConflict,
    add_custom_lane,
    allowed_stages,
    delete_custom_lane,
    read_custom_lanes,
    read_order,
    save_order,
)
from backend.storage.board_presence import heartbeat, snapshot
from backend.storage.case_store import (
    is_maintenance_report,
    workspace_attachment_lookup,
    workspace_attachment_write,
    workspace_revision,
    workspace_write,
)
from backend.storage.preference_store import (
    get_case_reads,
    get_quick_replies,
    get_workspace_board_prefs,
    save_case_reads,
    save_quick_replies,
    save_workspace_board_prefs,
)
from backend.storage.user_store import get_all_user_dicts, get_user

_REALTIME_SALT = "kurtex-workspace-ws-v1"
_presence_seq_lock = Lock()
_presence_seq = 0


def _bump_presence_seq() -> int:
    global _presence_seq
    with _presence_seq_lock:
        _presence_seq += 1
        return _presence_seq


def _get_presence_seq() -> int:
    with _presence_seq_lock:
        return _presence_seq


def register_workspace_routes(server):
    """Register all `/api/workspace/*` and `/ws/workspace` endpoints on `server.app`."""
    app = server.app
    sock = server.sock
    logger = server.logger

    def _data_dir() -> Path:
        return server.DATA_DIR

    def _workspace_can_manage(case, user):
        role = server._website_role(user.get("role"))
        if bool(case.get("is_test") or case.get("is_testing")) and role != "developer":
            return False
        return (
            role in ("developer", "super_admin")
            or bool(user.get("id") and str(case.get("agent_id")) == str(user["id"]))
            or bool(
                user.get("id")
                and not case.get("agent_id")
                and str(case.get("created_by_id") or "") == str(user["id"])
            )
        )

    def _workspace_actor():
        """Recheck current membership for writes; the login cookie is not role authority."""
        user = session.get("user")
        if not user:
            return None, (jsonify({"error": "Please sign in again."}), 401)
        token = session.get("workspace_csrf", "")
        if not getattr(g, "mobile_authenticated", False) and (
            not token or not hmac.compare_digest(token, request.headers.get("X-Workspace-CSRF", ""))
        ):
            return None, (jsonify({"error": "Reload the page before updating reports."}), 403)
        stored = get_user(user["id"])
        if not stored:
            return None, (jsonify({"error": "Your account no longer has access."}), 403)
        role = server._website_role(stored.get("role"))
        if "cases" not in server._load_role_permissions().get(role, []):
            return None, (jsonify({"error": "You do not have access to cases."}), 403)
        return {**stored, "id": user["id"], "role": role}, None

    def _workspace_actor_read():
        """Reads do not require a CSRF header; membership and case permissions still apply."""
        user = session.get("user")
        if not user:
            return None, (jsonify({"error": "Sign in required."}), 401)
        stored = get_user(user["id"])
        if not stored:
            return None, (jsonify({"error": "Access denied."}), 403)
        role = server._website_role(stored.get("role"))
        if "cases" not in server._load_role_permissions().get(role, []):
            return None, (jsonify({"error": "Access denied."}), 403)
        return {**stored, "id": user["id"], "role": role}, None

    def _workspace_board_version(actor):
        """Cheap O(1) SQLite version check; zero JSON parsing for unchanged boards."""
        try:
            ver = kurtex_db.get_version("cases_version", _data_dir())
            signature = f"{ver}:{actor['id']}:{actor['role']}"
            return hashlib.blake2s(signature.encode(), digest_size=12).hexdigest()
        except Exception:
            return None

    def _realtime_signer():
        return URLSafeTimedSerializer(app.secret_key, salt=_REALTIME_SALT)

    _ALLOWED_STAGES = {"", "jbs_plant", "meijer", "dealer_shop", "monitoring", "to_yard", "at_yard"}

    def _is_allowed_stage(stage_val: str) -> bool:
        clean = str(stage_val or "").strip()
        return clean in allowed_stages(_data_dir())
    _ALLOWED_PRIORITIES = {"low", "medium", "high", "normal"}
    _ALLOWED_QUICK_REPLY_ICONS = {
        "ph-phone-outgoing", "ph-wrench", "ph-package", "ph-receipt", "ph-check-circle",
        "ph-road-horizon", "ph-map-pin", "ph-truck", "ph-shield-check", "ph-chat-dots",
        "ph-lightning", "ph-clock", "ph-warning", "ph-note", "ph-currency-dollar",
        "ph-snowflake", "ph-thermometer",
    }

    def _resolve_active_agent(agent_id: str):
        target_id = str(agent_id or "").strip()
        return next(
            (
                u
                for u in get_all_user_dicts()
                if str(u["id"]) == target_id
                and u.get("role") in ("agent", "super_admin", "developer", "manager", "admin")
            ),
            None,
        )

    def _normalize_quick_replies(items) -> list[dict]:
        if not isinstance(items, list) or len(items) > 40:
            raise ValueError("Quick replies must be a list of up to 40 templates.")
        cleaned = []
        seen_slashes = set()
        for idx, raw in enumerate(items):
            if not isinstance(raw, dict):
                raise ValueError("Invalid quick reply entry.")
            label = re.sub(r"\s+", " ", str(raw.get("label") or "").strip())[:48]
            text_body = str(raw.get("text") or "").strip()[:600]
            if not label or not text_body:
                raise ValueError("Each quick reply needs a title and message.")
            raw_slash = str(raw.get("slash") or raw.get("command") or label).lstrip("/").strip().lower()
            slash = re.sub(r"[^a-z0-9_-]+", "", raw_slash)[:24] or f"reply{idx + 1}"
            base_slash = slash
            suffix = 2
            while slash in seen_slashes:
                slash = f"{base_slash[:20]}{suffix}"
                suffix += 1
            seen_slashes.add(slash)
            icon = str(raw.get("icon") or "ph-lightning").strip()
            if icon not in _ALLOWED_QUICK_REPLY_ICONS:
                icon = "ph-lightning"
            raw_id = re.sub(r"[^a-zA-Z0-9_-]+", "", str(raw.get("id") or ""))[:40]
            cleaned.append({
                "id": raw_id or f"qr-{slash}",
                "slash": slash,
                "label": label,
                "icon": icon,
                "text": text_body,
                "custom": bool(raw.get("custom", True)),
            })
        return cleaned

    @app.route("/api/workspace/agents")
    def api_workspace_agents():
        """Small, authenticated assignment roster; no role administration data."""
        actor, error = _workspace_actor_read()
        if error:
            return error
        all_users = get_all_user_dicts()
        shift_managed_ids = {
            str(u["id"])
            for u in all_users
            if isinstance(u, dict) and u.get("id") is not None and u.get("role") in ("agent", "super_admin")
        }
        agents = sorted(
            (
                {
                    "id": u["id"],
                    "name": u["name"],
                    "username": u.get("username", ""),
                    "role": server._website_role(u.get("role")),
                }
                for u in all_users
                if u.get("role") in ("agent", "super_admin", "developer", "manager", "admin") and u.get("name")
            ),
            key=lambda a: a["name"].casefold(),
        )
        try:
            case_reads = get_case_reads(actor["id"], data_dir=_data_dir())
            quick_replies = get_quick_replies(actor["id"], data_dir=_data_dir())
            workspace_prefs = get_workspace_board_prefs(actor["id"], data_dir=_data_dir())
        except Exception:
            case_reads = {}
            quick_replies = None
            workspace_prefs = {}
        try:
            on_shift_state = shift_store.get_on_shift_state()
        except Exception:
            on_shift_state = {}
        on_duty_set = {str(uid) for uid in (on_shift_state.get("on_duty_user_ids") or [])}
        active_shifts = [
            {
                "id": str(s.get("id") or ""),
                "name": str(s.get("name") or ""),
                "start_time": str(s.get("start_time") or ""),
                "end_time": str(s.get("end_time") or ""),
            }
            for s in (on_shift_state.get("active_shifts") or [])
            if isinstance(s, dict)
        ]
        user_shift_names: dict[str, list[str]] = {}
        for s in (on_shift_state.get("active_shifts") or []):
            if not isinstance(s, dict):
                continue
            sname = str(s.get("name") or "").strip()
            for uid in (s.get("user_ids") or []):
                if sname:
                    user_shift_names.setdefault(str(uid), []).append(sname)
        for a in agents:
            uid = str(a.get("id") or "")
            a["on_duty"] = uid in on_duty_set
            a["shift_managed"] = uid in shift_managed_ids
            a["shift_names"] = user_shift_names.get(uid, [])
        return jsonify({
            "agents": agents,
            "has_configured_shifts": bool(on_shift_state.get("has_configured_shifts")),
            "using_shift_fallback": bool(on_shift_state.get("using_fallback", True)),
            "active_shifts": active_shifts,
            "current_user_id": actor["id"],
            "current_user_name": actor.get("name") or actor.get("first_name") or actor.get("username") or "",
            "current_user_role": actor.get("role", "agent"),
            "can_reassign_any": actor["role"] in ("developer", "super_admin"),
            "case_reads": case_reads,
            "quick_replies": quick_replies,
            "workspace_prefs": workspace_prefs,
            "custom_lanes": read_custom_lanes(_data_dir()),
        })

    @app.route("/api/workspace/cases", methods=["POST"])
    @app.route("/api/workspace/cases/<case_id>", methods=["PATCH"])
    def api_workspace_write(case_id=None):
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "A report is required."}), 400
        try:
            def text(key, required=False, maximum=2000):
                value = data.get(key, "")
                if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
                    raise ValueError("Check the " + key.replace("_", " ") + " field.")
                return value.strip()

            if case_id is None:
                limits = {
                    "unit_number": 120,
                    "driver": 120,
                    "issue": 4000,
                    "group": 120,
                    "location": 2000,
                    "comments": 4000,
                    "request_id": 120,
                    "load_type": 40,
                    "pickup": 500,
                    "delivery": 500,
                    "setpoint": 80,
                    "current_temp": 80,
                    "temp_recorder": 16,
                    "part_number": 120,
                }
                payload = {
                    key: text(key, key in ("unit_number", "driver", "issue", "request_id"), maximum)
                    for key, maximum in limits.items()
                }
                if payload["load_type"] not in ("", "JBS Load", "Meijer Load", "Broker Load", "OpenDeck", "Empty"):
                    raise ValueError("Choose a valid load type.")
                if payload["temp_recorder"] not in ("", "Yes", "No"):
                    raise ValueError("Choose a valid temperature recorder value.")
                if payload["load_type"] == "Empty":
                    for key in ("pickup", "delivery", "setpoint", "current_temp", "temp_recorder"):
                        payload[key] = ""
                elif payload["load_type"] == "" or data.get("vehicle_type") != "reefer":
                    for key in ("setpoint", "current_temp", "temp_recorder"):
                        payload[key] = ""
                if data.get("vehicle_type") not in ("truck", "trailer", "reefer") or data.get("priority") not in (
                    "low",
                    "medium",
                    "high",
                    "critical",
                    "normal",
                ):
                    raise ValueError("Choose equipment and priority.")
                board_stage = str(data.get("board_stage") or "").strip()
                if not _is_allowed_stage(board_stage):
                    raise ValueError("Choose a valid workflow stage.")
                if "agent_id" in data:
                    requested_agent_id = str(data.get("agent_id") or "").strip()
                elif board_stage:
                    requested_agent_id = ""
                else:
                    requested_agent_id = str(actor["id"]).strip()

                if requested_agent_id.lower() in ("", "unassigned", "none"):
                    payload.update({
                        "id": str(uuid.uuid4()),
                        "vehicle_type": data["vehicle_type"],
                        "priority": data["priority"],
                        "board_stage": board_stage,
                        "unassigned": True,
                        "target_agent": None,
                    })
                else:
                    target = next(
                        (
                            u
                            for u in get_all_user_dicts()
                            if str(u["id"]) == requested_agent_id
                            and u.get("role") in ("agent", "super_admin", "developer", "manager", "admin")
                            and u.get("name")
                        ),
                        None,
                    )
                    if target is None:
                        raise ValueError("Choose a valid active agent for this case.")
                    if str(target["id"]) != str(actor["id"]) and actor["role"] not in ("developer", "super_admin"):
                        raise PermissionError("Only a manager can create cases for another agent.")
                    payload.update({
                        "id": str(uuid.uuid4()),
                        "vehicle_type": data["vehicle_type"],
                        "priority": data["priority"],
                        "board_stage": board_stage,
                        "target_agent": {
                            "id": target["id"],
                            "name": target["name"],
                            "username": target.get("username", ""),
                        },
                    })
            else:
                action = data.get("action")
                if action not in (
                    "close",
                    "reopen",
                    "edit",
                    "note",
                    "note_edit",
                    "note_delete",
                    "note_pin",
                    "reassign",
                    "stage",
                    "move",
                    "rank",
                    "set_test",
                ):
                    raise ValueError("Choose a valid report action.")
                payload = {"action": action, "revision": text("revision", action not in ("rank", "set_test"), 64)}

                def _opt_rank():
                    raw_rank = data.get("board_rank")
                    if raw_rank is None:
                        return None
                    if isinstance(raw_rank, bool) or not isinstance(raw_rank, (int, float)):
                        raise ValueError("Choose a valid card position.")
                    rank_val = float(raw_rank)
                    if not (-1e12 <= rank_val <= 1e12):
                        raise ValueError("Choose a valid card position.")
                    return rank_val

                if action == "rank":
                    rank_val = _opt_rank()
                    if rank_val is None:
                        raise ValueError("Choose a valid card position.")
                    payload["board_rank"] = rank_val
                elif action == "set_test":
                    if str(actor.get("role") or "").lower() != "developer":
                        raise PermissionError("Only developers can mark or unmark test cases.")
                    if type(data.get("is_test")) is not bool:
                        raise ValueError("Choose whether to mark this case as a test.")
                    payload["is_test"] = data["is_test"]
                elif action in ("note", "note_edit", "note_delete", "note_pin"):
                    if action in ("note", "note_edit"):
                        payload["text"] = text("text", False, 4000)
                    if action == "note":
                        files = data.get("file_ids", [])
                        if (
                            not isinstance(files, list)
                            or len(files) > 12
                            or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{32}", x) for x in files)
                        ):
                            raise ValueError("Choose valid conversation attachments.")
                        payload["file_ids"] = list(dict.fromkeys(files))
                        reply = data.get("reply_to_id", "")
                        if (
                            not isinstance(reply, str)
                            or len(reply) > 80
                            or (reply and not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", reply))
                        ):
                            raise ValueError("Choose a valid message to reply to.")
                        payload["reply_to_id"] = reply
                        if not payload["text"] and not payload["file_ids"]:
                            raise ValueError("Enter a message or attach a file.")
                    else:
                        note_id = data.get("note_id", "")
                        if note_id:
                            if not isinstance(note_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", note_id):
                                raise ValueError("Choose a valid comment.")
                            payload["note_id"] = note_id
                        else:
                            index = data.get("index")
                            if type(index) is not int or index < 0:
                                raise ValueError("Choose a valid comment.")
                            payload["index"] = index
                        if action == "note_pin":
                            if type(data.get("pinned")) is not bool:
                                raise ValueError("Choose a pin state.")
                            payload["pinned"] = data["pinned"]
                elif action in ("reassign", "move"):
                    target_id = str(data.get("agent_id") or "").strip()
                    target = next(
                        (
                            u
                            for u in get_all_user_dicts()
                            if str(u["id"]) == target_id
                            and u.get("role") in ("agent", "super_admin", "developer", "manager", "admin")
                        ),
                        None,
                    )
                    if not target:
                        raise ValueError("Choose an active agent from the team.")
                    payload["target"] = {
                        "id": target["id"],
                        "name": target["name"],
                        "username": target.get("username", ""),
                    }
                    if action == "move":
                        if not _is_allowed_stage(data.get("stage")):
                            raise ValueError("Choose a valid workflow stage.")
                        payload["stage"] = str(data.get("stage") or "").strip()
                    rank_val = _opt_rank()
                    if rank_val is not None:
                        payload["board_rank"] = rank_val
                    if data.get("allow_concurrent_move") is True:
                        payload["allow_concurrent_move"] = True
                elif action == "stage":
                    if not _is_allowed_stage(data.get("stage")):
                        raise ValueError("Choose a valid workflow stage.")
                    payload["stage"] = str(data.get("stage") or "").strip()
                    rank_val = _opt_rank()
                    if rank_val is not None:
                        payload["board_rank"] = rank_val
                    if data.get("allow_concurrent_move") is True:
                        payload["allow_concurrent_move"] = True
                elif action == "edit":
                    fields = data.get("fields")
                    allowed = {
                        "priority",
                        "location",
                        "description",
                        "issue_text",
                        "shop_info",
                        "amount",
                        "report_driver",
                        "unit_number",
                        "vehicle_type",
                        "comments",
                        "group_name",
                        "load_type",
                        "board_stage",
                        "pickup",
                        "delivery",
                        "part_number",
                        "setpoint",
                        "current_temp",
                        "temp_recorder",
                    }
                    if not isinstance(fields, dict) or not fields or set(fields) - allowed:
                        raise ValueError("Choose valid report fields.")
                    for key, value in fields.items():
                        if not isinstance(value, str) or len(value.strip()) > 4000:
                            raise ValueError("Check the report fields.")
                        if key in ("description", "issue_text") and not value.strip():
                            raise ValueError("The issue and description cannot be empty.")
                        if (
                            key in ("unit_number", "report_driver", "group_name", "part_number")
                            and len(value.strip()) > 200
                        ):
                            raise ValueError("The " + key.replace("_", " ") + " field is too long.")
                        if key == "shop_info" and len(value.strip()) > 1000:
                            raise ValueError("The shop info field is too long.")
                    if "priority" in fields and fields["priority"] not in ("low", "medium", "high", "critical", "normal"):
                        raise ValueError("Choose a valid priority.")
                    if "vehicle_type" in fields and fields["vehicle_type"] not in ("truck", "trailer", "reefer", ""):
                        raise ValueError("Choose valid equipment.")
                    if "amount" in fields:
                        raw = fields["amount"].strip()
                        if raw:
                            try:
                                amount = Decimal(raw)
                                if (
                                    not amount.is_finite()
                                    or amount < 0
                                    or amount > 10000000
                                    or amount.as_tuple().exponent < -2
                                ):
                                    raise ValueError("Amount must be between 0 and 10000000 with up to 2 decimals.")
                            except InvalidOperation:
                                raise ValueError("Enter a valid amount (for example, 125.50).")
                    if "load_type" in fields and fields["load_type"] not in (
                        "",
                        "JBS Load",
                        "Meijer Load",
                        "Broker Load",
                        "OpenDeck",
                        "Empty",
                    ):
                        raise ValueError("Choose a valid load type.")
                    if "board_stage" in fields and not _is_allowed_stage(fields["board_stage"]):
                        raise ValueError("Choose a valid workflow stage.")
                    if "temp_recorder" in fields and fields["temp_recorder"] not in ("", "Yes", "No"):
                        raise ValueError("Choose a valid temperature recorder value.")
                    payload["fields"] = {k: v.strip() for k, v in fields.items()}
                    base_fields = data.get("base_fields")
                    if isinstance(base_fields, dict):
                        payload["base_fields"] = {
                            k: str(v).strip()
                            for k, v in base_fields.items()
                            if k in allowed and isinstance(v, (str, int, float))
                        }
            case = workspace_write(actor, payload, case_id)
            resp = {"ok": True, "case": server.serialize_case(case), "revision": workspace_revision(case)}
            if payload.get("action") == "note" and isinstance(case.get("workspace_notes"), list) and case["workspace_notes"]:
                resp["note"] = server._normalize_case_notes([case["workspace_notes"][-1]])[0]
            return jsonify(resp)
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409 if case_id else 400
        except Exception:
            logger.exception("Workspace report write failed")
            return jsonify({"error": "The report could not be saved. Retry with your entered values."}), 503

    _ALLOWED_UPLOAD_EXTS = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
        ".pdf", ".txt", ".csv", ".tsv", ".json", ".log",
        ".doc", ".docx", ".mp4", ".mov", ".webm",
    }

    def _read_validated_upload(file_obj):
        if not file_obj or not file_obj.filename:
            return None, (jsonify({"error": "Select a file."}), 400)
        name = secure_filename(file_obj.filename)[:160]
        extension = Path(name).suffix.lower()
        if extension not in _ALLOWED_UPLOAD_EXTS:
            return None, (jsonify({"error": "Unsupported file type."}), 400)
        limit = (50 if extension in (".mp4", ".mov", ".webm") else 15) * 1024 * 1024
        content = file_obj.stream.read(limit + 1)
        if len(content) > limit:
            return None, (jsonify({"error": f"File exceeds {limit // (1024 * 1024)} MB."}), 413)
        return (name, extension, content), None

    @app.route("/api/workspace/cases/<case_id>/attachments", methods=["POST"])
    def api_workspace_attachment_upload(case_id):
        actor, error = _workspace_actor()
        if error:
            return error
        parsed, upload_err = _read_validated_upload(request.files.get("file"))
        if upload_err:
            return upload_err
        name, extension, content = parsed
        category = str(request.form.get("category") or "attachment").strip().lower()
        if category not in ("attachment", "proof"):
            return jsonify({"error": "Invalid document category."}), 400
        proof_type = str(request.form.get("proof_type") or "repair_proof").strip().lower()
        if category == "proof" and proof_type not in ("invoice", "repair_proof", "receipt", "other"):
            return jsonify({"error": "Invalid proof type."}), 400
        description = str(request.form.get("description") or "").strip()[:500]
        attachment_id = uuid.uuid4().hex
        folder = _data_dir() / "workspace_uploads" / case_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (attachment_id + extension)
        try:
            path.write_bytes(content)
            metadata = {
                "id": attachment_id,
                "blob_id": attachment_id,
                "name": name,
                "size": len(content),
                "extension": extension,
                "category": category,
                "description": description,
                "version_num": 1,
                "versions": [],
                "created_at": server._now_iso(),
                "uploaded_by": actor.get("name", ""),
                "uploaded_by_id": str(actor["id"]),
            }
            if category == "proof":
                metadata["proof_type"] = proof_type
            workspace_attachment_write(actor, case_id, attachment=metadata)
            return jsonify({"ok": True, "id": attachment_id, "category": category})
        except (LookupError, PermissionError, ValueError) as exc:
            path.unlink(missing_ok=True)
            return jsonify({"error": str(exc)}), 403
        except Exception:
            path.unlink(missing_ok=True)
            logger.exception("Attachment upload failed")
            return jsonify({"error": "Unable to save attachment."}), 503

    _INLINE_MIMES = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".csv": "text/csv; charset=utf-8",
        ".tsv": "text/tab-separated-values; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".log": "text/plain; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }

    @app.route("/api/workspace/cases/<case_id>/attachments/<attachment_id>", methods=["GET", "PATCH", "DELETE"])
    def api_workspace_attachment_file(case_id, attachment_id):
        if request.method in ("DELETE", "PATCH"):
            actor, error = _workspace_actor()
            if error:
                return error
        else:
            actor, error = _workspace_actor_read()
            if error:
                return error
        try:
            if request.method == "PATCH":
                data = request.get_json(silent=True) or {}
                patch_fields = {}
                for k in ("name", "description", "category", "proof_type"):
                    if k in data:
                        patch_fields[k] = data[k]
                if not patch_fields:
                    return jsonify({"error": "Provide fields to update."}), 400
                updated = workspace_attachment_write(
                    actor,
                    case_id,
                    update_id=attachment_id,
                    updates=patch_fields,
                )
                return jsonify({"ok": True, "attachment": updated})
            case, item = workspace_attachment_lookup(case_id, attachment_id)
            if (
                request.method == "DELETE"
                and bool(case.get("is_test") or case.get("is_testing"))
                and str(actor.get("role") or "").lower() != "developer"
            ):
                return jsonify({"error": "This case is marked as Test and is read-only for non-developers."}), 403
            if (
                request.method == "DELETE"
                and actor["role"] not in ("developer", "super_admin")
                and str(item.get("uploaded_by_id")) != str(actor["id"])
            ):
                return jsonify({"error": "Only the uploading agent may delete this attachment."}), 403
            folder = _data_dir() / "workspace_uploads" / case_id
            blob_id = str(item.get("blob_id") or attachment_id)
            path = folder / (blob_id + str(item.get("extension") or ""))
            if request.method == "DELETE":
                workspace_attachment_write(actor, case_id, remove_id=attachment_id)
                path.unlink(missing_ok=True)
                for ver in item.get("versions") or []:
                    if isinstance(ver, dict) and ver.get("blob_id"):
                        vpath = folder / (str(ver["blob_id"]) + str(ver.get("extension") or ""))
                        vpath.unlink(missing_ok=True)
                return jsonify({"ok": True})
            if not path.is_file():
                return jsonify({"error": "File unavailable."}), 404
            ext = str(item.get("extension") or "").lower()
            inline = request.args.get("inline") == "1"
            mime = _INLINE_MIMES.get(ext, "application/octet-stream") if inline else "application/octet-stream"
            return send_file(path, as_attachment=not inline, download_name=item["name"], mimetype=mime)
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/workspace/cases/<case_id>/attachments/<attachment_id>/versions", methods=["POST"])
    def api_workspace_attachment_new_version(case_id, attachment_id):
        actor, error = _workspace_actor()
        if error:
            return error
        parsed, upload_err = _read_validated_upload(request.files.get("file"))
        if upload_err:
            return upload_err
        name, extension, content = parsed
        blob_id = uuid.uuid4().hex
        folder = _data_dir() / "workspace_uploads" / case_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (blob_id + extension)
        try:
            path.write_bytes(content)
            updated = workspace_attachment_write(
                actor,
                case_id,
                new_version_id=attachment_id,
                version_meta={
                    "blob_id": blob_id,
                    "name": name,
                    "size": len(content),
                    "extension": extension,
                    "created_at": server._now_iso(),
                    "uploaded_by": actor.get("name", ""),
                    "uploaded_by_id": str(actor["id"]),
                },
            )
            return jsonify({"ok": True, "attachment": updated})
        except (PermissionError, LookupError, ValueError) as exc:
            path.unlink(missing_ok=True)
            code = 403 if isinstance(exc, PermissionError) else (404 if isinstance(exc, LookupError) else 400)
            return jsonify({"error": str(exc)}), code
        except Exception:
            path.unlink(missing_ok=True)
            logger.exception("Attachment version upload failed")
            return jsonify({"error": "Unable to upload new file version."}), 503

    @app.route("/api/workspace/cases/<case_id>/attachments/<attachment_id>/versions/<version_id>", methods=["GET"])
    def api_workspace_attachment_version_file(case_id, attachment_id, version_id):
        actor, error = _workspace_actor_read()
        if error:
            return error
        try:
            _case, item = workspace_attachment_lookup(case_id, attachment_id)
            versions = item.get("versions") or []
            ver = next((v for v in versions if isinstance(v, dict) and str(v.get("id")) == str(version_id)), None)
            if not ver:
                return jsonify({"error": "Version not found."}), 404
            blob_id = str(ver.get("blob_id") or ver.get("id"))
            ext = str(ver.get("extension") or item.get("extension") or "").lower()
            path = _data_dir() / "workspace_uploads" / case_id / (blob_id + ext)
            if not path.is_file():
                return jsonify({"error": "Version file unavailable."}), 404
            inline = request.args.get("inline") == "1"
            mime = _INLINE_MIMES.get(ext, "application/octet-stream") if inline else "application/octet-stream"
            return send_file(path, as_attachment=not inline, download_name=ver.get("name") or item["name"], mimetype=mime)
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.route("/api/workspace/cases/<case_id>/attachments/bulk", methods=["POST"])
    def api_workspace_attachments_bulk(case_id):
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True) or {}
        action = str(data.get("action") or "").strip().lower()
        ids = [str(x).strip() for x in (data.get("ids") or []) if str(x).strip()][:100]
        if action not in ("delete", "convert_to_proof", "convert_to_attachment") or not ids:
            return jsonify({"error": "Choose valid attachments and bulk action."}), 400
        proof_type = str(data.get("proof_type") or "repair_proof").strip().lower()
        processed = []
        skipped = []
        folder = _data_dir() / "workspace_uploads" / case_id
        for aid in ids:
            try:
                if action == "delete":
                    _case, item = workspace_attachment_lookup(case_id, aid)
                    workspace_attachment_write(actor, case_id, remove_id=aid)
                    blob_id = str(item.get("blob_id") or aid)
                    (folder / (blob_id + str(item.get("extension") or ""))).unlink(missing_ok=True)
                    for ver in item.get("versions") or []:
                        if isinstance(ver, dict) and ver.get("blob_id"):
                            (folder / (str(ver["blob_id"]) + str(ver.get("extension") or ""))).unlink(missing_ok=True)
                else:
                    target_cat = "proof" if action == "convert_to_proof" else "attachment"
                    workspace_attachment_write(
                        actor,
                        case_id,
                        update_id=aid,
                        updates={"category": target_cat, "proof_type": proof_type},
                    )
                processed.append(aid)
            except (PermissionError, LookupError, ValueError) as exc:
                skipped.append({"id": aid, "error": str(exc)})
        return jsonify({"ok": True, "processed": processed, "skipped": skipped})

    @app.route("/api/workspace/realtime-ticket", methods=["POST"])
    def api_workspace_realtime_ticket():
        actor, error = _workspace_actor_read()
        if error:
            return error
        payload = request.get_json(silent=True) or {}
        origin = str(payload.get("origin") or "")
        parsed = urllib.parse.urlsplit(origin)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            return jsonify({"error": "Invalid realtime origin."}), 400
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
            return jsonify({"error": "Realtime requires HTTPS."}), 400
        ticket = _realtime_signer().dumps({"uid": str(actor["id"]), "origin": origin})
        return jsonify({"ticket": ticket, "expires_in": 45})

    @sock.route("/ws/workspace")
    def workspace_socket(ws):
        try:
            raw = ws.receive(timeout=8)
            message = json.loads(raw or "null")
            if not isinstance(message, dict) or message.get("type") != "auth":
                ws.close()
                return
            signed = _realtime_signer().loads(str(message.get("ticket") or ""), max_age=45)
            if not hmac.compare_digest(str(signed.get("origin") or ""), str(request.headers.get("Origin") or "")):
                ws.close()
                return
            user_id = int(signed["uid"])
        except (ValueError, TypeError, KeyError, BadSignature, SignatureExpired, OSError):
            ws.close()
            return

        def authorized_actor():
            stored = get_user(user_id)
            if not stored:
                return None
            role = server._website_role(stored.get("role"))
            if "cases" not in server._load_role_permissions().get(role, []):
                return None
            return {"id": user_id, "role": role}

        actor = authorized_actor()
        if not actor:
            ws.close()
            return
        version = _workspace_board_version(actor)
        cursor = kurtex_db.get_latest_event_cursor(_data_dir())
        pres_seq = _get_presence_seq()
        ws.send(json.dumps({"type": "ready", "version": version, "cursor": cursor}))
        last_heartbeat = last_auth_check = last_version_check = connected_at = time.monotonic()
        while True:
            try:
                incoming = ws.receive(timeout=0.35)
                if incoming:
                    pass
                now = time.monotonic()
                if now - connected_at > 1800:
                    ws.close()
                    return
                if now - last_auth_check >= 25:
                    actor = authorized_actor()
                    if not actor:
                        ws.close()
                        return
                    last_auth_check = now
                if now - last_version_check >= 1.0:
                    last_version_check = now
                    next_version = _workspace_board_version(actor)
                    if next_version != version:
                        version = next_version
                        events = kurtex_db.get_events_since(cursor, limit=50, data_dir=_data_dir())
                        if events:
                            cursor = events[-1]["cursor"]
                        else:
                            cursor = kurtex_db.get_latest_event_cursor(_data_dir())
                        ws.send(json.dumps({"type": "changed", "version": version, "cursor": cursor, "events": events}))
                next_pres_seq = _get_presence_seq()
                if next_pres_seq != pres_seq:
                    pres_seq = next_pres_seq
                    ws.send(json.dumps({"type": "presence", "presence": snapshot(user_id, _data_dir())}))
                if now - last_heartbeat >= 20:
                    ws.send(json.dumps({"type": "heartbeat"}))
                    last_heartbeat = now
            except Exception:
                return

    @app.route("/api/workspace/order", methods=["GET", "PUT"])
    def api_workspace_order():
        """Shared manual card ordering with revision conflicts and role checking."""
        if request.method == "GET":
            actor, error = _workspace_actor_read()
            if error:
                return error
            return jsonify({"order": read_order(_data_dir()), "custom_lanes": read_custom_lanes(_data_dir())})
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Provide a card order."}), 400
        try:
            result = save_order(
                data.get("group"),
                data.get("ids"),
                data.get("version"),
                actor["id"],
                manager=actor["role"] in ("developer", "super_admin"),
                data_dir=_data_dir(),
            )
            return jsonify({"order": result})
        except OrderConflict as exc:
            return jsonify({"error": str(exc), "order": read_order(_data_dir())}), 409
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/workspace/lanes", methods=["GET", "POST"])
    def api_workspace_lanes():
        if request.method == "GET":
            actor, error = _workspace_actor_read()
            if error:
                return error
            return jsonify({"ok": True, "lanes": read_custom_lanes(_data_dir())})
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True) or {}
        try:
            res = add_custom_lane(data.get("name"), actor["id"], color=data.get("color") or "#7c3aed", data_dir=_data_dir())
            return jsonify({"ok": True, **res}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/workspace/lanes/<lane_id>", methods=["DELETE"])
    def api_workspace_lane_delete(lane_id):
        actor, error = _workspace_actor()
        if error:
            return error
        try:
            remaining = delete_custom_lane(lane_id, data_dir=_data_dir())
            return jsonify({"ok": True, "custom_lanes": remaining})
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/workspace/proofs", methods=["GET"])
    def api_workspace_proofs():
        actor, error = _workspace_actor_read()
        if error:
            return error
        is_dev = str(actor.get("role") or "").lower() == "developer"
        proof_filter = str(request.args.get("proof_type") or "").strip().lower()
        q = str(request.args.get("q") or "").strip().lower()
        items = []
        for raw_c in server.load_cases():
            if not is_maintenance_report(raw_c):
                continue
            if not is_dev and bool(raw_c.get("is_test") or raw_c.get("is_testing")):
                continue
            cid = str(raw_c.get("id") or "")
            unit = str(raw_c.get("unit_number") or "").strip()
            driver = str(raw_c.get("report_driver") or raw_c.get("driver_name") or "").strip()
            status = str(raw_c.get("status") or "reported")
            for a in (raw_c.get("workspace_attachments") or []):
                if not isinstance(a, dict) or not a.get("id"):
                    continue
                if str(a.get("category") or "").lower() != "proof":
                    continue
                pt = str(a.get("proof_type") or "repair_proof").lower()
                if proof_filter and proof_filter != "all" and pt != proof_filter:
                    continue
                hay = " ".join([
                    str(a.get("name") or ""),
                    str(a.get("description") or ""),
                    unit,
                    driver,
                    str(a.get("uploaded_by") or ""),
                ]).lower()
                if q and q not in hay:
                    continue
                items.append({
                    "id": a["id"],
                    "case_id": cid,
                    "unit_number": unit,
                    "driver": driver,
                    "case_status": status,
                    "name": a.get("name") or "Proof",
                    "extension": a.get("extension") or "",
                    "size": int(a.get("size") or 0),
                    "proof_type": pt,
                    "description": a.get("description") or "",
                    "uploaded_by": a.get("uploaded_by") or "Agent",
                    "created_at": a.get("created_at") or "",
                    "url": f"/api/workspace/cases/{urllib.parse.quote(cid, safe='')}/attachments/{urllib.parse.quote(str(a['id']), safe='')}",
                })
        items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return jsonify({"ok": True, "proofs": items[:150], "total": len(items)})

    @app.route("/api/workspace/presence", methods=["GET", "POST"])
    def api_workspace_presence():
        """Return live team members, or record a short-lived authenticated session."""
        if request.method == "GET":
            actor, error = _workspace_actor_read()
            if error:
                return error
            return jsonify(snapshot(actor["id"], _data_dir()))
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid collaboration session."}), 400
        try:
            heartbeat(
                data.get("session_id"),
                actor["id"],
                actor.get("name") or actor.get("first_name") or "Team member",
                data.get("action", "idle"),
                data.get("case_id", ""),
                data.get("field", ""),
                _data_dir(),
                draft=data.get("draft", ""),
            )
            _bump_presence_seq()
            return jsonify(snapshot(actor["id"], _data_dir()))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/workspace/board")
    def api_workspace_board():
        """Compact active board with incremental cursor delta sync."""
        actor, error = _workspace_actor_read()
        if error:
            return error
        since = request.args.get("since", "")[:64]
        version = _workspace_board_version(actor)
        latest_cursor = kurtex_db.get_latest_event_cursor(_data_dir())
        if since and version and hmac.compare_digest(since, version):
            return jsonify({"unchanged": True, "version": version, "cursor": latest_cursor})

        def _agent_key(value):
            raw = str(value or "").strip()
            return str(int(raw)) if raw.isdecimal() else raw

        def _summarize_board_case(c, roster_by_id):
            summary = server.serialize_case(c)
            assigned = roster_by_id.get(_agent_key(c.get("agent_id")))
            if assigned:
                name = str(assigned.get("name") or "").strip()
                if name and not name.isdecimal():
                    summary["agent"] = name
                username = str(assigned.get("username") or "").strip().lstrip("@")
                if username and not username.isdecimal():
                    summary["agent_username"] = username
            elif c.get("agent_id") is None or str(c.get("agent_id") or "").strip() == "":
                summary["agent"] = "Unassigned"
            elif str(summary.get("agent") or "").strip().isdecimal():
                summary["agent"] = "Unknown agent"
            summary["reported_at"] = c.get("reported_at") or c.get("opened_at") or ""
            summary["opened_at"] = c.get("opened_at") or ""
            summary["board_rank"] = float(c.get("board_rank") or 0.0)
            summary["revision"] = workspace_revision(c)
            summary["can_manage"] = _workspace_can_manage(c, actor)
            summary["can_reassign"] = summary["can_manage"]
            summary["can_comment"] = (not summary.get("is_test")) or (str(actor.get("role") or "").lower() == "developer")
            active_notes = [
                n
                for n in (c.get("workspace_notes") or [])
                if isinstance(n, dict) and not n.get("deleted_at")
            ]
            summary["notes_count"] = int(
                bool(c.get("comments") or (c.get("notes") and c.get("notes") != "case reported"))
            ) + len(active_notes)
            summary["note_markers"] = [
                {"at": str(n.get("created_at") or ""), "aid": str(n.get("author_id") or "")}
                for n in active_notes[-20:]
                if n.get("created_at")
            ]
            if active_notes:
                summary["last_note_at"] = str(active_notes[-1].get("created_at") or "")
                summary["last_note_author_id"] = str(active_notes[-1].get("author_id") or "")
                summary["last_note_author"] = str(active_notes[-1].get("author") or "")
            else:
                summary["last_note_at"] = ""
                summary["last_note_author_id"] = ""
                summary["last_note_author"] = ""
            summary["notes_search"] = " ".join(
                str(n.get("text") or "").strip()
                for n in active_notes
                if n.get("text")
            )[:800]
            summary["files_search"] = " ".join(
                str(f.get("name") or "").strip()
                for f in (c.get("workspace_attachments") or [])
                if isinstance(f, dict) and f.get("name")
            )[:400]
            return summary

        cursor_raw = request.args.get("cursor", "").strip()
        if cursor_raw.isdecimal():
            req_cursor = int(cursor_raw)
            if 0 < req_cursor <= latest_cursor and server.load_cases is server._DEFAULT_LOAD_CASES:
                events = kurtex_db.get_events_since(req_cursor, limit=85, data_dir=_data_dir())
                if 0 < len(events) <= 80:
                    changed_ids = list(dict.fromkeys(e["case_id"] for e in events if e.get("case_id")))
                    roster_by_id = {
                        _agent_key(u.get("id")): u
                        for u in get_all_user_dicts()
                        if isinstance(u, dict) and u.get("id") is not None
                    }
                    upserted = []
                    removed_ids = []
                    for cid in changed_ids:
                        raw_case = case_store.get_case(cid)
                        if not raw_case:
                            removed_ids.append(cid)
                            continue
                        c = normalize_case(raw_case)
                        active_board_item = (
                            is_maintenance_report(c)
                            and c.get("status") == "reported"
                            and not c.get("closed_at")
                            and (chicago_date_str(c.get("reported_at")) or "") >= "2026-10-12"
                        )
                        if active_board_item:
                            upserted.append(_summarize_board_case(c, roster_by_id))
                        else:
                            removed_ids.append(cid)
                    return jsonify({
                        "unchanged": False,
                        "delta": True,
                        "cursor": events[-1]["cursor"],
                        "version": version,
                        "upserted": upserted,
                        "removed_ids": removed_ids,
                    })

        def _read_board_cases():
            if (
                server.load_cases is server._DEFAULT_LOAD_CASES
                and case_store._storage_dir().resolve() == _data_dir().resolve()
            ):
                g.case_data_stale = False
                rows = case_store.get_workspace_board_cases("2026-10-12")
                return [normalize_case(c) for c in rows]
            cases = server.load_cases()
            return [
                c
                for c in cases
                if is_maintenance_report(c)
                and c.get("status") == "reported"
                and not c.get("closed_at")
                and (chicago_date_str(c.get("reported_at")) or "") >= "2026-10-12"
            ]

        board = _read_board_cases()
        after = _workspace_board_version(actor)
        if after != version:
            board = _read_board_cases()
            version = _workspace_board_version(actor)
            latest_cursor = kurtex_db.get_latest_event_cursor(_data_dir())
        else:
            version = after
        board.sort(key=lambda c: c.get("reported_at") or c.get("opened_at") or "", reverse=True)
        roster_by_id = {
            _agent_key(u.get("id")): u
            for u in get_all_user_dicts()
            if isinstance(u, dict) and u.get("id") is not None
        }
        summaries = [_summarize_board_case(c, roster_by_id) for c in board]
        return jsonify({
            "cases": summaries,
            "total": len(board),
            "has_more": False,
            "version": version,
            "cursor": latest_cursor,
            "unchanged": False,
        })

    _IGNORED_SHOP_VALUES = {"", "-", "--", "n/a", "na", "none", "tbd", "unknown", "test", "yard"}

    def _clean_shop(value):
        raw = re.sub(r"\s+", " ", str(value or "").strip())
        if raw.lower() in _IGNORED_SHOP_VALUES or len(raw) < 2:
            return ""
        return raw[:140]

    def _parse_amount(value):
        raw = re.sub(r"[^0-9.]", "", str(value or "").strip())
        if not raw:
            return None
        try:
            num = float(raw)
        except ValueError:
            return None
        return round(num, 2) if 0 < num <= 250000 else None

    _AI_NOISE_TERMS = {
        "the", "and", "for", "with", "from", "that", "this", "was", "are", "not", "but", "has", "have",
        "had", "into", "out", "too", "very", "about", "just", "now", "here", "there", "then", "than",
        "been", "being", "will", "would", "could", "should", "can", "did", "does", "all", "any", "some",
        "only", "also", "even", "still", "more", "much", "many", "most", "such", "other", "same", "each",
        "every", "both", "what", "which", "when", "where", "why", "how", "who", "trying", "try", "tried",
        "thing", "things", "stuff", "something", "anything", "nothing", "everything", "blah", "misc",
        "said", "says", "say", "tell", "told", "look", "looked", "looking", "make", "made", "know",
        "truck", "trailer", "reefer", "unit", "driver", "vehicle", "agent", "dispatch", "dispatcher",
        "case", "cases", "issue", "issues", "problem", "problems", "report", "reported", "repair",
        "repairs", "shop", "yard", "bay", "eta", "status", "priority", "stage", "load", "pickup",
        "delivery", "jbs", "meijer", "broker", "opendeck", "empty", "standard", "normal", "high",
        "medium", "low", "open", "closed", "done", "resolved", "assigned", "unassigned", "note",
        "notes", "comment", "comments", "update", "updated", "call", "called", "calling", "check",
        "checked", "checking", "wait", "waiting", "awaiting", "need", "needs", "needed", "please",
        "help", "info", "information", "none", "null", "true", "false", "text", "msg", "work",
        "working", "fix", "fixed", "broken", "send", "sent", "got", "get", "today", "yesterday",
        "morning", "night", "tonight", "asap", "test", "testing", "tests", "tester", "dummy",
        "sample", "example", "placeholder", "asdf", "qwerty", "fake", "debug", "temp", "n/a",
    }

    _AI_SYSTEM_KEYWORDS = (
        ("cooling", "Cooling", {"coolant", "cool", "radiator", "overheat", "thermostat", "antifreeze", "waterpump", "fan", "fanclutch", "degas", "reservoir", "heater"}),
        ("brakes", "Brakes", {"brake", "abs", "chamber", "slack", "adjust", "caliper", "rotor", "drum", "lining", "pad", "gladhand", "maxibrake"}),
        ("air", "Air / Suspension", {"airbag", "airline", "compressor", "governor", "leveling", "suspension", "leaf", "spring", "bushing", "shock", "dryer", "purge"}),
        ("electrical", "Electrical", {"battery", "alternator", "starter", "voltage", "volt", "crank", "solenoid", "wiring", "harness", "fuse", "relay", "dead", "ecm"}),
        ("emissions", "DEF / Aftertreatment", {"def", "dpf", "regen", "scr", "nox", "egr", "aftertreatment", "soot", "derate", "doc", "dosing", "exhaust"}),
        ("tires", "Tires & Wheels", {"tire", "steer", "blowout", "flat", "rim", "lug", "stud", "hub", "wheel", "seal", "bearing", "spindle", "align", "tread", "valve", "autoinflation", "inflation", "atis"}),
        ("reefer", "Reefer Unit", {"thermoking", "carrier", "shutdown", "defrost", "freon", "refriger", "condenser", "evaporator", "micro", "temp"}),
        ("fuel", "Fuel System", {"fuel", "injector", "primer", "priming", "separator", "gel"}),
        ("drivetrain", "Drivetrain / Axles", {"transmission", "clutch", "driveshaft", "ujoint", "differential", "axle", "yoke", "flywheel", "pto", "gearbox"}),
        ("body", "Body / Lights / DOT", {"windshield", "mirror", "headlight", "taillight", "marker", "clearance", "bumper", "door", "hinge", "latch", "mudflap", "wiper", "dot"}),
        ("towing", "Towing / Roadside", {"tow", "wrecker", "winch", "lockout", "roadside"}),
    )

    _AI_GENERIC_SYMPTOM_TOKENS = {
        "leak", "leaking", "leaks", "hose", "line", "lines", "light", "lights", "sensor", "switch",
        "valve", "clamp", "cap", "seal", "wire", "wiring", "pipe", "tube", "low", "high", "damage",
        "damaged", "loose", "missing", "worn", "bad", "old", "new", "front", "rear", "left", "right",
        "side", "driver", "passenger", "upper", "lower", "small", "big", "noise", "sound", "smell",
    }
    _SHARED_SHOP_SYSTEMS = {"brakes", "tires", "air", "electrical", "body", "towing"}
    _SHOP_STATE_RE = re.compile(
        r"(?:,\s*|\s+)(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
        r"MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|"
        r"AB|BC|MB|NB|NL|NS|ON|PE|QC|SK)\b"
    )

    def _equipment_family(vtype: str) -> str:
        v = str(vtype or "").strip().lower()
        if not v:
            return ""
        if v in ("truck", "tractor", "semi", "daycab", "sleeper", "cab"):
            return "truck"
        if v in ("reefer", "trailer", "dry_van", "dryvan", "van", "flatbed", "chassis"):
            return "trailer"
        return v

    def _extract_state_codes(*texts: str) -> set[str]:
        states: set[str] = set()
        for t in texts:
            s = str(t or "").strip()
            if not s:
                continue
            for m in _SHOP_STATE_RE.finditer(s):
                states.add(m.group(1).upper())
        return states

    _AI_CODE_RE = re.compile(r"\b(?:spn\s*[-]?\s*\d{2,5}|fmi\s*[-]?\s*\d{1,2}|[pbuc]\d{4}|[a-z]\d{2,3}|\d{3,5}-\d{1,3})\b", re.IGNORECASE)

    def _ai_stem(word: str) -> str:
        w = word.lower().strip("-_")
        if len(w) <= 3 or any(ch.isdigit() for ch in w):
            return w
        if w.endswith("ies") and len(w) > 5:
            return w[:-3] + "y"
        if w.endswith("ing") and len(w) > 6:
            stem = w[:-3]
            return stem if stem[-1:] not in ("a", "e", "i", "o", "u") else w[:-3]
        if w.endswith("ed") and len(w) > 5:
            return w[:-2]
        if w.endswith("es") and len(w) > 5:
            return w[:-2]
        if w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            return w[:-1]
        return w

    def _ai_extract_notes_text(c) -> str:
        parts = []
        for n in (c.get("workspace_notes") or []):
            if isinstance(n, dict) and not n.get("deleted_at") and str(n.get("text") or "").strip():
                parts.append(str(n["text"]))
        raw_notes = c.get("notes")
        if isinstance(raw_notes, str) and raw_notes.strip() and not raw_notes.lstrip().startswith("[{"):
            parts.append(raw_notes)
        return " ".join(parts)

    def _ai_tokenize(text: str) -> set[str]:
        tokens = set()
        for raw in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", (text or "").lower()):
            if raw in _AI_NOISE_TERMS or raw.isdigit():
                continue
            stemmed = _ai_stem(raw)
            if len(stemmed) < 3 or stemmed in _AI_NOISE_TERMS:
                continue
            tokens.add(stemmed)
        return tokens

    def _ai_extract_codes(text: str) -> set[str]:
        return {
            re.sub(r"[\s-]+", "", m.group(0)).upper()
            for m in _AI_CODE_RE.finditer(text or "")
        }

    def _ai_detect_systems(raw_text: str, tokens: set[str]) -> tuple[set[str], dict[str, str]]:
        raw_words = set(re.findall(r"[a-z0-9]+", (raw_text or "").lower()))
        probe = raw_words | tokens
        keys = set()
        labels = {}
        for key, label, kws in _AI_SYSTEM_KEYWORDS:
            if probe & kws:
                keys.add(key)
                labels[key] = label
        return keys, labels

    def _ai_extract_signals(c) -> dict:
        primary_text = " ".join(
            str(c.get(k) or "") for k in ("issue_text", "description", "part_number", "location")
        )
        note_text = _ai_extract_notes_text(c)
        full_text = f"{primary_text} {note_text}"
        primary_tokens = _ai_tokenize(primary_text)
        note_tokens = _ai_tokenize(note_text)
        all_tokens = primary_tokens | note_tokens
        codes = _ai_extract_codes(full_text)
        systems, system_labels = _ai_detect_systems(full_text, all_tokens)
        return {
            "primary_tokens": primary_tokens,
            "note_tokens": note_tokens,
            "all_tokens": all_tokens,
            "codes": codes,
            "systems": systems,
            "system_labels": system_labels,
        }

    def _resolution_note(c):
        notes = [n for n in (c.get("workspace_notes") or []) if isinstance(n, dict) and not n.get("deleted_at") and str(n.get("text") or "").strip()]
        if notes:
            txt = re.sub(r"\s+", " ", str(notes[-1]["text"])).strip()
            return "" if txt.lower() in _IGNORED_SHOP_VALUES else txt[:160]
        for key in ("notes", "comments"):
            val = c.get(key)
            if not isinstance(val, str):
                continue
            txt = re.sub(r"\s+", " ", val.strip())
            if txt and not txt.startswith("[{") and txt.lower() not in _IGNORED_SHOP_VALUES:
                return txt[:160]
        return ""

    @app.route("/api/workspace/cases/<case_id>/ai-insights")
    def api_workspace_case_ai_insights(case_id):
        """Return AI-ready historical shop suggestions, cost band, unit repeat alerts, and similar cases."""
        actor, error = _workspace_actor_read()
        if error:
            return error
        raw_id = str(case_id or "").strip()
        if not raw_id:
            return jsonify({"error": "Missing case ID."}), 400
        try:
            if (
                server.load_cases is server._DEFAULT_LOAD_CASES
                and case_store._storage_dir().resolve() == _data_dir().resolve()
            ):
                raw_case, ambiguous = case_store.get_case_or_prefix(raw_id)
                if ambiguous:
                    return jsonify({"error": "Ambiguous case ID."}), 409
                target = normalize_case(raw_case) if raw_case else None
            else:
                matches = [
                    c for c in server.load_cases()
                    if str(c.get("id") or "") == raw_id or str(c.get("id") or "").startswith(raw_id)
                ]
                if len(matches) > 1:
                    return jsonify({"error": "Ambiguous case ID."}), 409
                target = matches[0] if matches else None
            if not target or not is_maintenance_report(target):
                return jsonify({"error": "Case not found."}), 404

            target_id = str(target.get("id") or "")
            raw_unit = str(target.get("unit_number") or "").strip()
            target_is_test = server.is_history_test_case(target)
            unit = "" if (target_is_test or raw_unit.lower() in _IGNORED_SHOP_VALUES) else raw_unit
            unit_norm = unit.lower()
            vtype = str(target.get("vehicle_type") or "").strip().lower()
            resolved_statuses = {"done", "resolved", "closed"}
            now_ct = chicago_now()

            q_sig = _ai_extract_signals(target)
            has_signal = not target_is_test and bool(
                q_sig["primary_tokens"] or q_sig["codes"] or q_sig["systems"] or len(q_sig["all_tokens"]) >= 2
            )

            all_cases = [
                c for c in server.load_cases()
                if isinstance(c, dict)
                and not server.is_testing(c)
                and not server.is_history_test_case(c)
            ]

            candidates = []
            unit_cases = []
            doc_freq = {}
            for c in all_cases:
                cid = str(c.get("id") or "")
                if cid == target_id:
                    continue
                c_unit = str(c.get("unit_number") or "").strip()
                same_unit = bool(unit_norm and c_unit.lower() == unit_norm)
                if same_unit:
                    unit_cases.append(c)
                if not has_signal:
                    continue
                c_sig = _ai_extract_signals(c)
                if not (c_sig["all_tokens"] or c_sig["codes"] or c_sig["systems"]):
                    continue
                for tok in c_sig["all_tokens"]:
                    doc_freq[tok] = doc_freq.get(tok, 0) + 1
                candidates.append((c, same_unit, c_sig))

            total_docs = max(1, len(candidates))

            def _idf(tok: str) -> float:
                df = doc_freq.get(tok, 0)
                return math.log((total_docs + 1.5) / (df + 0.5)) + 0.35

            q_weights = {}
            for t in q_sig["all_tokens"]:
                q_weights[t] = _idf(t) * (1.6 if t in q_sig["primary_tokens"] else 0.85)
            q_norm = math.sqrt(sum(w * w for w in q_weights.values())) or 1.0

            scored = []
            q_family = _equipment_family(vtype)
            for c, same_unit, c_sig in candidates:
                overlap_tok = q_sig["all_tokens"] & c_sig["all_tokens"]
                overlap_primary = q_sig["primary_tokens"] & c_sig["primary_tokens"]
                overlap_codes = q_sig["codes"] & c_sig["codes"]
                overlap_systems = q_sig["systems"] & c_sig["systems"]
                specific_overlap = {t for t in overlap_tok if t not in _AI_GENERIC_SYMPTOM_TOKENS}
                c_vtype = str(c.get("vehicle_type") or "").strip().lower()
                c_family = _equipment_family(c_vtype)
                equip_conflict = bool(q_family and c_family and q_family != c_family)

                # Never match cases across disjoint repair systems or incompatible truck/reefer equipment on generic words (e.g. "hose" + "leak").
                if (
                    q_sig["systems"]
                    and c_sig["systems"]
                    and not overlap_systems
                    and not overlap_codes
                    and not same_unit
                ):
                    continue
                if (
                    equip_conflict
                    and not same_unit
                    and not overlap_codes
                    and not (overlap_systems & _SHARED_SHOP_SYSTEMS)
                ):
                    continue

                passes_gate = bool(
                    overlap_codes
                    or (overlap_primary and overlap_systems)
                    or len(specific_overlap) >= 2
                    or (len(specific_overlap) >= 1 and len(overlap_tok) >= 2 and not equip_conflict)
                    or (overlap_primary and len(q_sig["all_tokens"]) <= 2 and not equip_conflict)
                    or (same_unit and (overlap_tok or overlap_systems))
                )
                if not passes_gate:
                    continue

                c_weights = {}
                for t in c_sig["all_tokens"]:
                    c_weights[t] = _idf(t) * (1.6 if t in c_sig["primary_tokens"] else 0.85)
                c_norm = math.sqrt(sum(w * w for w in c_weights.values())) or 1.0
                dot = sum(q_weights.get(t, 0.0) * c_weights.get(t, 0.0) for t in overlap_tok)
                cos_sim = dot / (q_norm * c_norm) if overlap_tok else 0.0

                sem_sim = cos_sim
                if overlap_codes:
                    sem_sim += 0.34
                if overlap_systems:
                    sem_sim += 0.16 * min(2, len(overlap_systems)) / max(1, len(q_sig["systems"]))
                sem_sim = min(0.99, sem_sim)

                if sem_sim < 0.16 and not overlap_codes:
                    continue

                c_status = str(c.get("status") or "").strip().lower()
                is_resolved = c_status in resolved_statuses or bool(c.get("closed_at"))
                c_vtype = str(c.get("vehicle_type") or "").strip().lower()
                shop = _clean_shop(c.get("shop_info"))

                rank_score = sem_sim
                if is_resolved:
                    rank_score += 0.14
                if same_unit:
                    rank_score += 0.18
                if vtype and c_vtype == vtype:
                    rank_score += 0.07
                if shop:
                    rank_score += 0.06

                pct = min(99, max(24, int(round(sem_sim * 100))))
                matched_tags = sorted(overlap_codes) + sorted(overlap_tok)[:max(0, 6 - len(overlap_codes))]
                matched_sys_labels = [
                    q_sig["system_labels"].get(k, k.title()) for k in sorted(overlap_systems)
                ]
                scored.append((rank_score, sem_sim, is_resolved, same_unit, pct, matched_tags[:6], matched_sys_labels, c, shop))

            scored.sort(key=lambda x: (x[0], x[2], x[1]), reverse=True)

            # Aggregate shop recommendations from genuinely matched fleet repairs
            shop_buckets = {}
            all_amounts = []
            seen_shop_cases = set()
            for sc, _sem, is_res, same_u, _pct, _m, _sys, c, shop in scored[:40]:
                cid = str(c.get("id") or "")
                amt = _parse_amount(c.get("amount"))
                if amt is not None and is_res:
                    all_amounts.append(amt)
                if not shop or cid in seen_shop_cases:
                    continue
                seen_shop_cases.add(cid)
                key = shop.lower()
                bucket = shop_buckets.setdefault(key, {
                    "shop_name": shop,
                    "count": 0,
                    "resolved_count": 0,
                    "unit_count": 0,
                    "score_sum": 0.0,
                    "max_score": 0.0,
                    "matched_systems": set(),
                    "vehicle_types": set(),
                    "amounts": [],
                    "last_date": "",
                    "sample_issue": "",
                })
                bucket["count"] += 1
                if is_res:
                    bucket["resolved_count"] += 1
                if same_u:
                    bucket["unit_count"] += 1
                bucket["score_sum"] += sc
                bucket["max_score"] = max(bucket["max_score"], sc)
                bucket["matched_systems"].update(_sys)
                cv = str(c.get("vehicle_type") or "").strip().lower()
                if cv:
                    bucket["vehicle_types"].add(cv)
                if amt is not None:
                    bucket["amounts"].append(amt)
                when = str(c.get("closed_at") or c.get("reported_at") or c.get("opened_at") or "")
                if when > bucket["last_date"]:
                    bucket["last_date"] = when
                    bucket["sample_issue"] = str(c.get("issue_text") or c.get("description") or "")[:90]

            suggested_shops = []
            for item in sorted(
                shop_buckets.values(),
                key=lambda s: (-s["unit_count"], -s["resolved_count"], -round(s["score_sum"], 2), -s["count"]),
            )[:4]:
                if item["unit_count"] == 0 and item["max_score"] < 0.28 and not item["matched_systems"]:
                    continue
                amts = item["amounts"]
                reason_bits = []
                if item["unit_count"]:
                    reason_bits.append(f"serviced Unit #{unit} before" if unit else "same-unit repair")
                if item["resolved_count"]:
                    reason_bits.append(f"{item['resolved_count']} resolved match{'es' if item['resolved_count'] != 1 else ''}")
                sys_list = sorted(item["matched_systems"])[:2]
                if sys_list and not item["unit_count"]:
                    reason_bits.append(" · ".join(sys_list))
                suggested_shops.append({
                    "shop_name": item["shop_name"],
                    "count": item["count"],
                    "resolved_count": item["resolved_count"],
                    "unit_count": item["unit_count"],
                    "vehicle_types": sorted(item["vehicle_types"]),
                    "matched_systems": sorted(item["matched_systems"]),
                    "avg_amount": round(sum(amts) / len(amts), 2) if amts else None,
                    "min_amount": round(min(amts), 2) if amts else None,
                    "max_amount": round(max(amts), 2) if amts else None,
                    "last_used": server.fmt_dt(item["last_date"]),
                    "sample_issue": item["sample_issue"],
                    "reason": " · ".join(reason_bits) if reason_bits else "matched fleet repair",
                    "source_type": "fleet_history",
                    "live": False,
                })

            is_preload = request.args.get("preload") == "1"
            force_live = request.args.get("force_live") == "1"
            route_shop_search = {
                "status": "skipped_test" if target_is_test else ("skipped_preload" if is_preload else "no_route"),
                "provider": "",
                "anchors": [],
                "shops": [],
                "note": "",
            }
            if not target_is_test and not is_preload:
                live_fn = getattr(server, "_search_live_route_shops", search_live_repair_shops)
                try:
                    route_res = live_fn(
                        pickup=str(target.get("pickup") or ""),
                        delivery=str(target.get("delivery") or ""),
                        location=str(target.get("location") or ""),
                        vehicle_type=vtype,
                        systems=q_sig["systems"],
                        force=force_live,
                    )
                except Exception:
                    route_res = {
                        "status": "unavailable",
                        "provider": "",
                        "anchors": [],
                        "shops": [],
                        "note": "Live repair shop search could not reach external map services right now.",
                    }
                if isinstance(route_res, dict):
                    route_shop_search = {
                        "status": str(route_res.get("status") or "unavailable"),
                        "provider": str(route_res.get("provider") or ""),
                        "anchors": route_res.get("anchors") if isinstance(route_res.get("anchors"), list) else [],
                        "shops": route_res.get("shops") if isinstance(route_res.get("shops"), list) else [],
                        "note": str(route_res.get("note") or ""),
                        "searched_at": str(route_res.get("searched_at") or ""),
                    }
                existing_norm = []
                for idx, s in enumerate(suggested_shops):
                    norm = re.sub(r"[^a-z0-9]+", "", str(s.get("shop_name") or "").lower())
                    existing_norm.append((idx, norm))
                for ls in route_shop_search.get("shops", [])[:8]:
                    if not isinstance(ls, dict):
                        continue
                    ls_name = str(ls.get("shop") or ls.get("shop_name") or "").strip()
                    if not ls_name:
                        continue
                    ls_norm = re.sub(r"[^a-z0-9]+", "", ls_name.lower())
                    match_idx = None
                    for idx, ex_norm in existing_norm:
                        if ex_norm and ls_norm and (ex_norm == ls_norm or (len(ex_norm) >= 6 and len(ls_norm) >= 6 and (ex_norm in ls_norm or ls_norm in ex_norm))):
                            match_idx = idx
                            break
                    dist_val = ls.get("distance_miles")
                    reason_bits = [
                        bit for bit in (
                            str(ls.get("anchor_label") or "").strip(),
                            f"{dist_val:.1f} mi" if isinstance(dist_val, (int, float)) else "",
                            str(ls.get("category") or "").strip(),
                        ) if bit
                    ]
                    live_meta = {
                        "live": True,
                        "address": str(ls.get("address") or ""),
                        "phone": str(ls.get("phone") or ""),
                        "website": str(ls.get("website") or ""),
                        "opening_hours": str(ls.get("opening_hours") or ""),
                        "rating": ls.get("rating"),
                        "rating_count": int(ls.get("rating_count") or 0),
                        "category": str(ls.get("category") or ""),
                        "anchor_key": str(ls.get("anchor_key") or ""),
                        "anchor_label": str(ls.get("anchor_label") or ""),
                        "anchor_query": str(ls.get("anchor_query") or ""),
                        "distance_miles": dist_val,
                        "map_url": str(ls.get("map_url") or ""),
                        "provider": str(ls.get("provider") or route_shop_search.get("provider") or ""),
                    }
                    if match_idx is not None:
                        target_entry = suggested_shops[match_idx]
                        target_entry["source_type"] = "fleet_and_live"
                        target_entry.update(live_meta)
                        if reason_bits:
                            target_entry["reason"] = f"{target_entry['reason']} · {' · '.join(reason_bits[:2])}"
                    else:
                        new_entry = {
                            "shop_name": ls_name,
                            "count": 0,
                            "resolved_count": 0,
                            "unit_count": 0,
                            "avg_amount": None,
                            "min_amount": None,
                            "max_amount": None,
                            "last_used": "",
                            "sample_issue": "",
                            "reason": " · ".join(reason_bits) if reason_bits else "Live route shop",
                            "source_type": "live_route",
                            **live_meta,
                        }
                        suggested_shops.append(new_entry)
                        existing_norm.append((len(suggested_shops) - 1, ls_norm))

            unit_cases.sort(key=lambda c: str(c.get("reported_at") or c.get("opened_at") or ""), reverse=True)
            recent_30d = 0
            unit_resolved = 0
            recent_unit_summaries = []
            for uc in unit_cases:
                st = str(uc.get("status") or "").strip().lower()
                is_res = st in resolved_statuses or bool(uc.get("closed_at"))
                if is_res:
                    unit_resolved += 1
                dt = chicago_timestamp(uc.get("reported_at") or uc.get("opened_at"))
                if dt and 0 <= (now_ct - dt).days <= 30:
                    recent_30d += 1
                if len(recent_unit_summaries) < 4:
                    amt = _parse_amount(uc.get("amount"))
                    recent_unit_summaries.append({
                        "id": str(uc.get("id") or ""),
                        "issue_text": str(uc.get("issue_text") or uc.get("description") or "Maintenance issue")[:110],
                        "status": str(uc.get("status") or "reported"),
                        "is_resolved": is_res,
                        "shop_info": _clean_shop(uc.get("shop_info")),
                        "amount": amt,
                        "date": server.fmt_dt(uc.get("closed_at") or uc.get("reported_at") or uc.get("opened_at")),
                    })

            unit_insights = {
                "unit_number": unit,
                "total": len(unit_cases),
                "resolved": unit_resolved,
                "active": max(0, len(unit_cases) - unit_resolved),
                "recent_30d": recent_30d,
                "repeat_warning": recent_30d >= 1 or len(unit_cases) >= 3,
                "recent_cases": recent_unit_summaries,
            }

            similar_cases = []
            for _sc, _sem, is_res, same_u, pct, matched, matched_sys, c, shop in scored[:6]:
                amt = _parse_amount(c.get("amount"))
                similar_cases.append({
                    "id": str(c.get("id") or ""),
                    "unit_number": str(c.get("unit_number") or ""),
                    "vehicle_type": str(c.get("vehicle_type") or ""),
                    "issue_text": str(c.get("issue_text") or c.get("description") or "Maintenance issue")[:130],
                    "status": str(c.get("status") or "reported"),
                    "is_resolved": is_res,
                    "shop_info": shop,
                    "amount": amt,
                    "reported_at": server.fmt_dt(c.get("reported_at") or c.get("opened_at")),
                    "closed_at": server.fmt_dt(c.get("closed_at")),
                    "agent": str(c.get("agent_name") or c.get("agent") or "Unassigned"),
                    "matched": matched,
                    "matched_systems": matched_sys,
                    "match_pct": pct,
                    "same_unit": same_u,
                    "resolution_note": _resolution_note(c),
                })

            cost_estimate = None
            if all_amounts:
                cost_estimate = {
                    "count": len(all_amounts),
                    "avg": round(sum(all_amounts) / len(all_amounts), 2),
                    "min": round(min(all_amounts), 2),
                    "max": round(max(all_amounts), 2),
                }

            bullets = []
            if not has_signal:
                bullets.append("Test or placeholder text detected — add real mechanical symptoms, fault codes, or part names for AI matching.")
            else:
                if q_sig["system_labels"] or q_sig["codes"]:
                    tags = ([f"Codes: {', '.join(sorted(q_sig['codes'])[:3])}"] if q_sig["codes"] else []) + (
                        [f"Systems: {', '.join(sorted(q_sig['system_labels'].values())[:3])}"] if q_sig["system_labels"] else []
                    )
                    if tags:
                        bullets.append("Detected triage signals — " + " · ".join(tags) + ".")
                if suggested_shops:
                    top = suggested_shops[0]
                    if top.get("source_type") == "live_route":
                        bullets.append(
                            f"Live route shop found: {top['shop_name']} ({top['reason']})."
                        )
                    else:
                        bullets.append(
                            f"Best historical shop candidate: {top['shop_name']} ({top['reason']})."
                        )
                        live_extra = next((s for s in suggested_shops if s.get("source_type") == "live_route"), None)
                        if live_extra:
                            bullets.append(
                                f"Live route option: {live_extra['shop_name']} ({live_extra['reason']})."
                            )
                else:
                    bullets.append("No previous shop matched these exact symptoms yet — check nearby preferred vendor.")
                if cost_estimate:
                    bullets.append(
                        f"Typical repair spend across {cost_estimate['count']} resolved match{'es' if cost_estimate['count'] != 1 else ''}: avg ${cost_estimate['avg']:,.0f} (${cost_estimate['min']:,.0f}–${cost_estimate['max']:,.0f})."
                    )
                if unit_insights["recent_30d"] >= 1:
                    bullets.append(
                        f"Repeat alert: Unit #{unit or '?'} had {unit_insights['recent_30d']} other breakdown(s) in the last 30 days — verify prior repair warranty."
                    )
                elif similar_cases:
                    res_cnt = sum(1 for x in similar_cases if x["is_resolved"])
                    bullets.append(f"Found {len(similar_cases)} verified fleet match{'es' if len(similar_cases) != 1 else ''} ({res_cnt} resolved) to guide troubleshooting.")

            if not has_signal and not target_is_test and route_shop_search.get("shops"):
                top_live = route_shop_search["shops"][0]
                bullets.append(
                    f"Live route shop near {top_live.get('anchor_query') or 'route'}: {top_live.get('shop')}."
                )

            triage = {
                "headline": (
                    "Test / non-fleet case" if target_is_test
                    else f"Top match: {suggested_shops[0]['shop_name']}" if suggested_shops
                    else "Low-detail / test case" if not has_signal
                    else f"{len(similar_cases)} verified fleet match{'es' if len(similar_cases) != 1 else ''}" if similar_cases
                    else "No similar past cases yet"
                ),
                "detected_systems": sorted(q_sig["system_labels"].values()) if has_signal else [],
                "detected_codes": sorted(q_sig["codes"])[:5] if has_signal else [],
                "low_signal": not has_signal,
                "bullets": bullets,
            }

            llm_advice = None
            llm_error = ""
            if request.args.get("llm") == "1":
                try:
                    context_payload = {
                        "unit": unit,
                        "equipment": vtype or "truck",
                        "issue": str(target.get("issue_text") or target.get("description") or "")[:350],
                        "location": str(target.get("location") or "")[:120],
                        "detected_systems": triage["detected_systems"],
                        "detected_codes": triage["detected_codes"],
                        "suggested_shops": suggested_shops[:3],
                        "cost_estimate": cost_estimate,
                        "unit_recent_30d": unit_insights["recent_30d"],
                        "similar_resolved": [
                            {
                                "unit": s["unit_number"],
                                "issue": s["issue_text"],
                                "shop": s["shop_info"],
                                "amount": s["amount"],
                                "note": s["resolution_note"],
                            }
                            for s in similar_cases[:4]
                        ],
                    }
                    raw_llm = server._cf_ai([
                        {
                            "role": "system",
                            "content": (
                                "You are a senior fleet maintenance dispatcher assistant. Based strictly on the provided case and "
                                "verified historical matches, give 3 concise actionable bullets (1: likely root cause/check, "
                                "2: best shop/bay routing using suggested_shops if present, 3: cost/warranty watchout). Plain text bullets starting with '• '."
                            ),
                        },
                        {"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)},
                    ], max_tokens=220, temperature=0.15)
                    llm_advice = str(raw_llm or "").strip()
                except Exception as exc:
                    llm_error = str(exc) or "AI advisor is currently unavailable."

            return jsonify({
                "ok": True,
                "case_id": target_id,
                "unit_number": unit,
                "vehicle_type": vtype,
                "unit_insights": unit_insights,
                "suggested_shops": suggested_shops,
                "route_shop_search": route_shop_search,
                "cost_estimate": cost_estimate,
                "similar_cases": similar_cases,
                "triage": triage,
                "llm_advice": llm_advice,
                "llm_error": llm_error,
            })
        except Exception:
            logger.exception("Workspace AI insights failed for %s", raw_id)
            return jsonify({"error": "Unable to load AI insights for this case."}), 503

    @app.route("/api/workspace/quick-replies", methods=["GET", "PUT"])
    def api_workspace_quick_replies():
        if request.method == "GET":
            actor, error = _workspace_actor_read()
            if error:
                return error
            return jsonify({
                "ok": True,
                "quick_replies": get_quick_replies(actor["id"], data_dir=_data_dir()),
            })
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid quick replies payload."}), 400
        if data.get("reset") is True:
            save_quick_replies(actor["id"], None, data_dir=_data_dir())
            return jsonify({"ok": True, "quick_replies": None})
        try:
            cleaned = _normalize_quick_replies(data.get("quick_replies"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        saved = save_quick_replies(actor["id"], cleaned, data_dir=_data_dir())
        return jsonify({"ok": True, "quick_replies": saved})

    @app.route("/api/workspace/read-state", methods=["POST"])
    def api_workspace_read_state():
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid read state payload."}), 400
        default_ts = utc_now().isoformat()
        updates = {}
        raw_reads = data.get("reads")
        if isinstance(raw_reads, dict):
            for cid, ts in list(raw_reads.items())[:300]:
                key = str(cid or "").strip()[:120]
                if key:
                    val = str(ts or "").strip()[:64] or default_ts
                    updates[key] = val
        elif data.get("case_id"):
            key = str(data.get("case_id") or "").strip()[:120]
            val = str(data.get("seen_at") or "").strip()[:64] or default_ts
            if key:
                updates[key] = val
        if not updates:
            return jsonify({"error": "Choose at least one case to mark as read."}), 400
        merged = save_case_reads(actor["id"], updates, data_dir=_data_dir())
        return jsonify({"ok": True, "case_reads": merged})

    @app.route("/api/workspace/cases/bulk", methods=["POST"])
    def api_workspace_cases_bulk():
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid bulk update payload."}), 400
        raw_ids = data.get("case_ids")
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
            return jsonify({"error": "Select between 1 and 100 cases."}), 400
        case_ids = list(dict.fromkeys(str(cid).strip() for cid in raw_ids if str(cid or "").strip()))
        if not case_ids:
            return jsonify({"error": "Select at least one valid case."}), 400

        action = str(data.get("action") or "update").strip().lower()
        if action not in ("update", "close", "reopen"):
            return jsonify({"error": "Choose a valid bulk action."}), 400

        target = None
        has_agent = "agent_id" in data and str(data.get("agent_id") or "").strip() != ""
        has_stage = "board_stage" in data and data.get("board_stage") is not None
        has_priority = "priority" in data and str(data.get("priority") or "").strip() != ""

        if action == "update":
            if not (has_agent or has_stage or has_priority):
                return jsonify({"error": "Choose an agent, workflow stage, or priority to apply."}), 400
            if has_agent:
                target = _resolve_active_agent(str(data.get("agent_id") or ""))
                if not target:
                    return jsonify({"error": "Choose an active agent from the team."}), 400
                target_dict = {
                    "id": target["id"],
                    "name": target["name"],
                    "username": target.get("username", ""),
                }
            else:
                target_dict = None
            stage_val = None
            if has_stage:
                stage_val = str(data.get("board_stage") or "").strip()
                if not _is_allowed_stage(stage_val):
                    return jsonify({"error": "Choose a valid workflow stage."}), 400
            prio_val = None
            if has_priority:
                prio_val = str(data.get("priority") or "").strip().lower()
                if prio_val not in _ALLOWED_PRIORITIES:
                    return jsonify({"error": "Choose a valid priority."}), 400

        updated_ids = []
        skipped = []
        for cid in case_ids:
            raw_case = case_store.get_case(cid)
            if not raw_case:
                skipped.append({"id": cid, "reason": "Case not found."})
                continue
            try:
                cur = raw_case
                if action == "close":
                    workspace_write(
                        actor,
                        {"action": "close", "revision": workspace_revision(cur)},
                        cid,
                    )
                elif action == "reopen":
                    workspace_write(
                        actor,
                        {"action": "reopen", "revision": workspace_revision(cur)},
                        cid,
                    )
                else:
                    if prio_val is not None and str(cur.get("priority") or "normal") != prio_val:
                        cur = workspace_write(
                            actor,
                            {
                                "action": "edit",
                                "revision": workspace_revision(cur),
                                "fields": {"priority": prio_val},
                                "base_fields": {"priority": str(cur.get("priority") or "")},
                            },
                            cid,
                        )
                    if target_dict is not None and stage_val is not None:
                        workspace_write(
                            actor,
                            {
                                "action": "move",
                                "revision": workspace_revision(cur),
                                "target": target_dict,
                                "stage": stage_val,
                                "allow_concurrent_move": True,
                            },
                            cid,
                        )
                    elif target_dict is not None:
                        workspace_write(
                            actor,
                            {
                                "action": "reassign",
                                "revision": workspace_revision(cur),
                                "target": target_dict,
                                "allow_concurrent_move": True,
                            },
                            cid,
                        )
                    elif stage_val is not None:
                        workspace_write(
                            actor,
                            {
                                "action": "stage",
                                "revision": workspace_revision(cur),
                                "stage": stage_val,
                                "allow_concurrent_move": True,
                            },
                            cid,
                        )
                updated_ids.append(cid)
            except (PermissionError, LookupError, ValueError) as exc:
                skipped.append({"id": cid, "reason": str(exc)})
            except Exception:
                logger.exception("Bulk update failed for %s", cid)
                skipped.append({"id": cid, "reason": "Unexpected save error."})

        return jsonify({
            "ok": True,
            "updated_ids": updated_ids,
            "updated_count": len(updated_ids),
            "skipped": skipped,
        })

    @app.route("/api/workspace/preferences", methods=["GET", "PUT"])
    def api_workspace_preferences():
        """Read or update per-user Live Board UI, alerts, AI, toolbar & saved views preferences."""
        if request.method == "GET":
            actor, error = _workspace_actor_read()
            if error:
                return error
            return jsonify({
                "ok": True,
                "preferences": get_workspace_board_prefs(actor["id"], data_dir=_data_dir()),
            })
        actor, error = _workspace_actor()
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid workspace preferences payload."}), 400
        saved = save_workspace_board_prefs(actor["id"], data, data_dir=_data_dir())
        return jsonify({
            "ok": True,
            "preferences": saved,
        })

    @app.route("/api/workspace/shops", methods=["GET"])
    def api_workspace_shops():
        """Return deduplicated repair shop directory, optionally scored & filtered for a target case."""
        actor, error = _workspace_actor_read()
        if error:
            return error
        q = re.sub(r"\s+", " ", str(request.args.get("q") or "").strip().lower())
        q_terms = [t for t in q.split(" ") if t]
        try:
            limit = int(request.args.get("limit") or 80)
        except ValueError:
            limit = 80
        limit = max(1, min(limit, 150))

        target_case_id = str(request.args.get("case_id") or "").strip()
        context_only = str(request.args.get("context_only") or "").strip().lower() in ("1", "true", "yes")

        target_case = None
        all_cases = [normalize_case(raw_case) for raw_case in server.load_cases()]
        if target_case_id:
            for c in all_cases:
                if str(c.get("id") or "") == target_case_id or str(c.get("request_id") or "") == target_case_id:
                    target_case = c
                    break

        target_unit = str(request.args.get("unit_number") or (target_case or {}).get("unit_number") or "").strip().lower()
        target_vtype = str(request.args.get("vehicle_type") or (target_case or {}).get("vehicle_type") or "").strip().lower()
        target_family = _equipment_family(target_vtype)
        target_issue = " ".join(filter(None, [
            str(request.args.get("issue_text") or ""),
            str((target_case or {}).get("issue_text") or ""),
            str((target_case or {}).get("description") or ""),
            str((target_case or {}).get("part_number") or ""),
        ])).strip()
        target_pickup = str(request.args.get("pickup") or (target_case or {}).get("pickup") or "").strip()
        target_delivery = str(request.args.get("delivery") or (target_case or {}).get("delivery") or "").strip()
        target_location = str(request.args.get("location") or (target_case or {}).get("location") or "").strip()
        has_case_context = bool(target_case_id or target_case or target_unit or target_vtype or target_issue or target_pickup or target_delivery or target_location)

        target_tokens = _ai_tokenize(target_issue) if target_issue else set()
        target_systems, _ = _ai_detect_systems(target_issue, target_tokens) if target_issue else (set(), {})
        target_codes = _ai_extract_codes(target_issue) if target_issue else set()
        target_states = _extract_state_codes(target_pickup, target_delivery, target_location)

        buckets: dict[str, dict] = {}
        for c in all_cases:
            if not is_maintenance_report(c) or server.is_history_test_case(c):
                continue
            shop = _clean_shop(c.get("shop_info") or c.get("dealer_shop"))
            if not shop:
                continue
            canon = re.sub(r"[^a-z0-9]+", " ", shop.lower()).strip()
            if not canon or canon in _IGNORED_SHOP_VALUES:
                continue
            bucket = buckets.setdefault(
                canon,
                {
                    "shop_name": shop,
                    "count": 0,
                    "resolved_count": 0,
                    "amounts": [],
                    "last_used": "",
                    "vehicle_types": [],
                    "sample_units": [],
                    "sample_issue": "",
                    "states": set(),
                    "systems": set(),
                    "tokens": set(),
                    "codes": set(),
                    "records": [],
                },
            )
            bucket["count"] += 1
            if len(shop) > len(bucket["shop_name"]):
                bucket["shop_name"] = shop
            status = str(c.get("status") or "").strip().lower()
            is_res = status in ("done", "closed", "resolved")
            if is_res:
                bucket["resolved_count"] += 1
            amt = _parse_amount(c.get("amount"))
            if amt is not None:
                bucket["amounts"].append(amt)
            ts = str(c.get("closed_at") or c.get("updated_at") or c.get("reported_at") or c.get("opened_at") or "").strip()
            issue_txt = re.sub(r"\s+", " ", str(c.get("issue_text") or c.get("description") or "").strip())
            if ts > bucket["last_used"]:
                bucket["last_used"] = ts
                if issue_txt:
                    bucket["sample_issue"] = issue_txt[:80]
            vtype = str(c.get("vehicle_type") or "").strip().lower()
            if vtype and vtype not in bucket["vehicle_types"] and len(bucket["vehicle_types"]) < 4:
                bucket["vehicle_types"].append(vtype)
            unit_num = str(c.get("unit_number") or "").strip()
            if unit_num and unit_num not in bucket["sample_units"] and len(bucket["sample_units"]) < 3:
                bucket["sample_units"].append(unit_num)
            if not bucket["sample_issue"] and issue_txt:
                bucket["sample_issue"] = issue_txt[:80]

            c_sig = _ai_extract_signals(c)
            bucket["states"].update(_extract_state_codes(shop, c.get("location"), c.get("pickup"), c.get("delivery")))
            bucket["systems"].update(c_sig["systems"])
            bucket["tokens"].update(c_sig["all_tokens"])
            bucket["codes"].update(c_sig["codes"])
            if len(bucket["records"]) < 8:
                bucket["records"].append({
                    "unit": unit_num,
                    "vehicle_type": vtype,
                    "issue": issue_txt[:80],
                    "systems": sorted(c_sig["systems"]),
                    "resolved": is_res,
                    "ts": ts,
                })

        results = []
        for b in buckets.values():
            hay = " ".join([
                b["shop_name"].lower(),
                " ".join(b["vehicle_types"]),
                " ".join(sorted(b["states"])).lower(),
                " ".join(sorted(b["systems"])).lower(),
                " ".join(b["sample_units"]).lower(),
                b["sample_issue"].lower(),
            ])
            if q_terms and not all(term in hay for term in q_terms):
                continue

            relevance_score = 0
            match_reasons: list[str] = []
            matched_unit = ""
            matched_issue = ""

            if has_case_context:
                same_unit_rec = None
                if target_unit:
                    same_unit_rec = next((r for r in b["records"] if r["unit"].lower() == target_unit), None)
                    if same_unit_rec or any(u.lower() == target_unit for u in b["sample_units"]):
                        relevance_score += 12
                        matched_unit = target_unit.upper()
                        matched_issue = (same_unit_rec or {}).get("issue", "")
                        match_reasons.append(f"Serviced Unit {matched_unit}")

                overlap_states = sorted(target_states & b["states"])
                if overlap_states:
                    relevance_score += 6
                    match_reasons.append(f"Route state {', '.join(overlap_states[:2])}")

                b_families = {_equipment_family(v) for v in b["vehicle_types"] if _equipment_family(v)}
                same_family = bool(target_family and target_family in b_families)
                family_conflict = bool(target_family and b_families and target_family not in b_families)

                overlap_codes = sorted(target_codes & b["codes"])
                if overlap_codes:
                    relevance_score += 8
                    match_reasons.append(f"Code {', '.join(overlap_codes[:2])}")

                overlap_sys = sorted(target_systems & b["systems"])
                specific_tok = {t for t in (target_tokens & b["tokens"]) if t not in _AI_GENERIC_SYMPTOM_TOKENS}

                if not (
                    family_conflict
                    and not same_unit_rec
                    and not overlap_codes
                    and not (set(overlap_sys) & _SHARED_SHOP_SYSTEMS)
                    and not overlap_states
                ):
                    if overlap_sys:
                        relevance_score += 4 * len(overlap_sys)
                        if same_family:
                            relevance_score += 2
                        match_reasons.append("Matched " + ", ".join(overlap_sys[:2]))
                    if len(specific_tok) >= 2:
                        relevance_score += 3 + len(specific_tok)
                    elif same_family and len(specific_tok) >= 1 and (not target_systems or not b["systems"] or overlap_sys):
                        relevance_score += 3

                    if not matched_issue and (overlap_sys or overlap_codes or len(specific_tok) >= 2):
                        best_rec = next(
                            (r for r in b["records"] if (not target_family or _equipment_family(r["vehicle_type"]) == target_family) and (set(r["systems"]) & set(overlap_sys))),
                            None,
                        )
                        if best_rec:
                            matched_issue = best_rec["issue"]

                # When opening autocomplete without typing a search query (`not q_terms`) inside a specific case,
                # NEVER push unrelated historical shops (like another unit's Reefer job on a Truck case).
                if not q_terms and (bool(target_case_id) or context_only):
                    if relevance_score < 5:
                        continue

            amts = b["amounts"]
            results.append({
                "shop_name": b["shop_name"],
                "count": b["count"],
                "resolved_count": b["resolved_count"],
                "avg_amount": round(sum(amts) / len(amts), 2) if amts else None,
                "last_used": b["last_used"],
                "vehicle_types": b["vehicle_types"],
                "sample_units": b["sample_units"],
                "sample_issue": b["sample_issue"],
                "states": sorted(b["states"]),
                "systems": sorted(b["systems"]),
                "records": b["records"][:5],
                "relevance_score": relevance_score,
                "match_reasons": match_reasons,
                "matched_unit": matched_unit,
                "matched_issue": matched_issue,
            })

        results.sort(
            key=lambda x: (x["relevance_score"], x["resolved_count"] * 2 + x["count"], x["last_used"], len(x["shop_name"])),
            reverse=True,
        )
        return jsonify({
            "ok": True,
            "shops": results[:limit],
            "total": len(results),
        })

    # Expose functions on `server` for backwards compatibility with callers/tests
    server._workspace_can_manage = _workspace_can_manage
    server._workspace_actor = _workspace_actor
    server._workspace_actor_read = _workspace_actor_read
    server._workspace_board_version = _workspace_board_version
    server._realtime_signer = _realtime_signer
    server.api_workspace_agents = api_workspace_agents
    server.api_workspace_write = api_workspace_write
    server.api_workspace_attachment_upload = api_workspace_attachment_upload
    server.api_workspace_attachment_file = api_workspace_attachment_file
    server.api_workspace_realtime_ticket = api_workspace_realtime_ticket
    server.workspace_socket = workspace_socket
    server.api_workspace_order = api_workspace_order
    server.api_workspace_presence = api_workspace_presence
    server.api_workspace_board = api_workspace_board
    server.api_workspace_case_ai_insights = api_workspace_case_ai_insights
    server._search_live_route_shops = search_live_repair_shops
    server.api_workspace_preferences = api_workspace_preferences
    server.api_workspace_shops = api_workspace_shops
