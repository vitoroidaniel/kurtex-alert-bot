# Workspace collaboration protocol

This backend complements the Kurtex Desktop V7 presence frontend patch.

## Transport
Authenticated WebSocket: `/ws/workspace`. First client frame remains `{"type":"auth","ticket":"..."}`. Tickets are short-lived, signed, and origin-bound. The backend sends `{"type":"ready","version":"..."}` and a snapshot of active peers.

Clients publish lightweight events:
```json
{"type":"presence","action":"dragging","case_id":"KT-1042","field":""}
```
Allowed actions: `viewing`, `editing`, `dragging`, `idle`, `left`. `editing` may include a short field key. Only server-side authenticated identity and stored display name are used.

Peers receive:
```json
{"type":"presence","agent_id":"123","agent_name":"Agent","action":"dragging","case_id":"KT-1042","field":""}
```
When a peer becomes inactive, receivers get `{"type":"presence","agent_id":"123","action":"left"}`. Clients should also expire stale UI indicators locally.

## Operational constraints
- Presence is ephemeral and does **not** lock cases. HTTP writes remain protected by the existing revision checks and case-store file lock.
- Messages are capped at 512 bytes and limited to approximately six updates per second per connection.
- Activity expires after 30 seconds; clients should re-announce current activity periodically.
- The presence hub is process-local. This is compatible with the current single Gunicorn worker / 32-thread deployment. **Do not deploy multiple API workers or replicas without replacing the hub with Redis pub/sub and a shared TTL store.**
- The bounded event log causes lagging sockets to reconnect rather than silently lose events.
- Do not merge until unit tests and two-browser testing verify live drag/edit indicators and reconnect behavior.

## Verification
```sh
python -m py_compile api/server.py backend/core/workspace_presence.py
python -m unittest discover -s tests -p 'test_workspace_presence.py' -v
```
