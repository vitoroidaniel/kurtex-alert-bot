"""Kurtex Live Board (Case Workspace) API & Realtime WebSocket routes."""
from __future__ import annotations

import hashlib
import hmac
import json
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

from backend.core.app_time import chicago_date_str
from backend.core.case_snapshot import normalize as normalize_case
from backend.storage import case_store, db as kurtex_db
from backend.storage.board_order import OrderConflict, read_order, save_order
from backend.storage.board_presence import heartbeat, snapshot
from backend.storage.case_store import (
    is_maintenance_report,
    workspace_attachment_lookup,
    workspace_attachment_write,
    workspace_revision,
    workspace_write,
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
        return (
            server._website_role(user.get("role")) in ("developer", "super_admin")
            or bool(user.get("id") and str(case.get("agent_id")) == str(user["id"]))
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

    @app.route("/api/workspace/agents")
    def api_workspace_agents():
        """Small, authenticated assignment roster; no role administration data."""
        actor, error = _workspace_actor_read()
        if error:
            return error
        agents = sorted(
            (
                {"id": u["id"], "name": u["name"], "username": u.get("username", "")}
                for u in get_all_user_dicts()
                if u.get("role") in ("agent", "super_admin") and u.get("name")
            ),
            key=lambda a: a["name"].casefold(),
        )
        return jsonify({
            "agents": agents,
            "current_user_id": actor["id"],
            "can_reassign_any": actor["role"] in ("developer", "super_admin"),
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
                requested_agent_id = str(data.get("agent_id") or actor["id"]).strip()
                target = next(
                    (
                        u
                        for u in get_all_user_dicts()
                        if str(u["id"]) == requested_agent_id
                        and u.get("role") in ("agent", "super_admin")
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
                    "edit",
                    "note",
                    "note_edit",
                    "note_delete",
                    "note_pin",
                    "reassign",
                    "stage",
                    "move",
                    "rank",
                ):
                    raise ValueError("Choose a valid report action.")
                payload = {"action": action, "revision": text("revision", action != "rank", 64)}

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
                            if str(u["id"]) == target_id and u.get("role") in ("agent", "super_admin")
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
                        if data.get("stage") not in (
                            "",
                            "jbs_plant",
                            "meijer",
                            "dealer_shop",
                            "monitoring",
                            "to_yard",
                            "at_yard",
                        ):
                            raise ValueError("Choose a valid workflow stage.")
                        payload["stage"] = data["stage"]
                    rank_val = _opt_rank()
                    if rank_val is not None:
                        payload["board_rank"] = rank_val
                    if data.get("allow_concurrent_move") is True:
                        payload["allow_concurrent_move"] = True
                elif action == "stage":
                    allowed_stages = {"", "jbs_plant", "meijer", "dealer_shop", "monitoring", "to_yard", "at_yard"}
                    if data.get("stage") not in allowed_stages:
                        raise ValueError("Choose a valid workflow stage.")
                    payload["stage"] = data["stage"]
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
                            key in ("unit_number", "report_driver", "group_name", "shop_info", "part_number")
                            and len(value.strip()) > 200
                        ):
                            raise ValueError("The " + key.replace("_", " ") + " field is too long.")
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
                    if "board_stage" in fields and fields["board_stage"] not in (
                        "",
                        "jbs_plant",
                        "meijer",
                        "dealer_shop",
                        "monitoring",
                        "to_yard",
                        "at_yard",
                    ):
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
            return jsonify({"ok": True, "case": server.serialize_case(case), "revision": workspace_revision(case)})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409 if case_id else 400
        except Exception:
            logger.exception("Workspace report write failed")
            return jsonify({"error": "The report could not be saved. Retry with your entered values."}), 503

    @app.route("/api/workspace/cases/<case_id>/attachments", methods=["POST"])
    def api_workspace_attachment_upload(case_id):
        actor, error = _workspace_actor()
        if error:
            return error
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "Select a file."}), 400
        name = secure_filename(file.filename)[:160]
        extension = Path(name).suffix.lower()
        allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".doc", ".docx", ".mp4", ".mov", ".webm"}
        if extension not in allowed:
            return jsonify({"error": "Unsupported file type."}), 400
        category = str(request.form.get("category") or "attachment").strip().lower()
        if category not in ("attachment", "proof"):
            return jsonify({"error": "Invalid document category."}), 400
        proof_type = str(request.form.get("proof_type") or "repair_proof").strip().lower()
        if category == "proof" and proof_type not in ("invoice", "repair_proof", "receipt", "other"):
            return jsonify({"error": "Invalid proof type."}), 400
        description = str(request.form.get("description") or "").strip()[:500]
        limit = (50 if extension in (".mp4", ".mov", ".webm") else 15) * 1024 * 1024
        content = file.stream.read(limit + 1)
        if len(content) > limit:
            return jsonify({"error": f"File exceeds {limit // (1024 * 1024)} MB."}), 413
        attachment_id = uuid.uuid4().hex
        folder = _data_dir() / "workspace_uploads" / case_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (attachment_id + extension)
        try:
            path.write_bytes(content)
            metadata = {
                "id": attachment_id,
                "name": name,
                "size": len(content),
                "extension": extension,
                "category": category,
                "created_at": server._now_iso(),
                "uploaded_by": actor.get("name", ""),
                "uploaded_by_id": str(actor["id"]),
            }
            if category == "proof":
                metadata.update({"proof_type": proof_type, "description": description})
            workspace_attachment_write(actor, case_id, attachment=metadata)
            return jsonify({"ok": True, "id": attachment_id, "category": category})
        except (LookupError, PermissionError, ValueError) as exc:
            path.unlink(missing_ok=True)
            return jsonify({"error": str(exc)}), 403
        except Exception:
            path.unlink(missing_ok=True)
            logger.exception("Attachment upload failed")
            return jsonify({"error": "Unable to save attachment."}), 503

    @app.route("/api/workspace/cases/<case_id>/attachments/<attachment_id>", methods=["GET", "DELETE"])
    def api_workspace_attachment_file(case_id, attachment_id):
        if request.method == "DELETE":
            actor, error = _workspace_actor()
            if error:
                return error
        else:
            actor, error = _workspace_actor_read()
            if error:
                return error
        try:
            case, item = workspace_attachment_lookup(case_id, attachment_id)
            if (
                request.method == "DELETE"
                and actor["role"] not in ("developer", "super_admin")
                and str(item.get("uploaded_by_id")) != str(actor["id"])
            ):
                return jsonify({"error": "Only the uploading agent may delete this attachment."}), 403
            path = _data_dir() / "workspace_uploads" / case_id / (attachment_id + item["extension"])
            if request.method == "DELETE":
                workspace_attachment_write(actor, case_id, remove_id=attachment_id)
                path.unlink(missing_ok=True)
                return jsonify({"ok": True})
            if not path.is_file():
                return jsonify({"error": "File unavailable."}), 404
            return send_file(path, as_attachment=True, download_name=item["name"], mimetype="application/octet-stream")
        except (LookupError, PermissionError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404

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
        last_heartbeat = last_auth_check = connected_at = time.monotonic()
        while True:
            try:
                incoming = ws.receive(timeout=0.25)
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
            return jsonify({"order": read_order(_data_dir())})
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
            elif str(summary.get("agent") or "").strip().isdecimal():
                summary["agent"] = "Unknown agent"
            summary["reported_at"] = c.get("reported_at") or c.get("opened_at") or ""
            summary["opened_at"] = c.get("opened_at") or ""
            summary["board_rank"] = float(c.get("board_rank") or 0.0)
            summary["revision"] = workspace_revision(c)
            summary["can_manage"] = _workspace_can_manage(c, actor)
            summary["can_reassign"] = summary["can_manage"]
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
                            and not server.is_testing(c)
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
                return [normalize_case(c) for c in rows if not server.is_testing(c)]
            cases = server.load_cases()
            return [
                c
                for c in cases
                if is_maintenance_report(c)
                and c.get("status") == "reported"
                and not c.get("closed_at")
                and (chicago_date_str(c.get("reported_at")) or "") >= "2026-10-12"
                and not server.is_testing(c)
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
