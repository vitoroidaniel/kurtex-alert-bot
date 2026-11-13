"""Ephemeral, authenticated workspace collaboration presence.

This hub is intentionally process-local. The deployed API currently runs one
Gunicorn worker with threads; use Redis pub/sub before scaling to multiple API
workers or replicas. Presence never changes case data.
"""
import re
import time
import uuid
from collections import deque
from threading import RLock

_ALLOWED = frozenset(("viewing", "editing", "dragging", "idle", "left"))
_CASE_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_FIELD = re.compile(r"^[A-Za-z0-9_.:@-]{0,64}$")


class PresenceHub:
    def __init__(self, ttl=30, event_limit=1024, clock=None):
        self.ttl = ttl
        self.clock = clock or time.monotonic
        self.lock = RLock()
        self.connections = {}
        self.events = deque(maxlen=event_limit)
        self.seq = 0

    def _emit(self, event):
        self.seq += 1
        self.events.append((self.seq, event))

    def _latest(self, uid):
        matches = [(key, item) for key, item in self.connections.items()
                   if item["agent_id"] == uid and item.get("action")]
        return max(matches, key=lambda pair: pair[1]["at"])[1] if matches else None

    def _leave_if_absent(self, uid):
        if self._latest(uid) is None:
            self._emit({"type": "presence", "agent_id": uid, "action": "left"})

    def _expire(self):
        now = self.clock()
        expired = [item for item in self.connections.values()
                   if item.get("action") and now - item["at"] > self.ttl]
        affected = set()
        for item in expired:
            affected.add(item["agent_id"])
            item["action"] = None
            item.pop("event", None)
        for uid in affected:
            current = self._latest(uid)
            if current is None:
                self._leave_if_absent(uid)
            else:
                self._emit(current["event"])

    def join(self, uid):
        with self.lock:
            self._expire()
            key = uuid.uuid4().hex
            self.connections[key] = {"agent_id": str(uid), "at": self.clock(), "action": None}
            return key

    def snapshot(self, own_uid):
        with self.lock:
            self._expire()
            peers = {item["agent_id"] for item in self.connections.values()
                     if item.get("action") and item["agent_id"] != str(own_uid)}
            return self.seq, [dict(self._latest(uid)["event"]) for uid in sorted(peers)]

    def publish(self, key, action, case_id="", field="", name=""):
        with self.lock:
            entry = self.connections.get(key)
            if entry is None or action not in _ALLOWED:
                return False
            if not isinstance(case_id, str) or not isinstance(field, str):
                return False
            if action in ("viewing", "editing", "dragging"):
                if not _CASE_ID.fullmatch(case_id) or not _FIELD.fullmatch(field):
                    return False
            elif case_id or field:
                return False
            self._expire()
            uid = entry["agent_id"]
            entry["at"] = self.clock()
            if action in ("idle", "left"):
                was_active = bool(entry.get("action"))
                entry["action"] = None
                entry.pop("event", None)
                if was_active:
                    current = self._latest(uid)
                    if current is None:
                        self._leave_if_absent(uid)
                    else:
                        self._emit(current["event"])
            else:
                event = {"type": "presence", "agent_id": uid, "agent_name": str(name)[:48],
                         "action": action, "case_id": case_id, "field": field}
                entry["action"] = action
                entry["event"] = event
                self._emit(event)
            return True

    def changes(self, cursor, own_uid):
        with self.lock:
            self._expire()
            latest = self.seq
            if self.events and cursor < self.events[0][0] - 1:
                # A slow socket should reconnect rather than receive an incomplete log.
                return latest, None
            items = [dict(event) for index, event in self.events
                     if index > cursor and event.get("agent_id") != str(own_uid)]
            return latest, items

    def leave(self, key):
        with self.lock:
            entry = self.connections.pop(key, None)
            if entry and entry.get("action"):
                current = self._latest(entry["agent_id"])
                if current is None:
                    self._leave_if_absent(entry["agent_id"])
                else:
                    self._emit(current["event"])


workspace_presence = PresenceHub()
