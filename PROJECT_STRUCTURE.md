# Kurtex project structure

- `bot.py` — Telegram application bootstrap only.
- `bot_app/handlers/` — Telegram agent/admin/report/scheduler behavior.
- `backend/core/` — configuration, time zones, shifts, dashboard data snapshots.
- `backend/storage/` — durable Railway-volume case/user storage.
- `backend/ai/` — AI chat learning/research/video plus automatic Fleet Knowledge indexing.
  - `fleet_knowledge.py` keeps historical fleet evidence separate from manual AI Review.
  - `ai_learning.py` stores developer-reviewed AI feedback/verified lessons.
- `templates/` — Flask pages and partials.
- `static/js/core/` — browser app/API/state foundation.
- `static/js/features/` — AI, cases, reports, fleet, analytics and developer knowledge/review UI.
- `static/js/platform/` — mobile-specific behavior.
- `static/css/` — core, feature, platform and auth styling.
- `data/` — bundled non-secret reference data only. Runtime data belongs in `DATA_DIR`.
- `tests/` — regression/unit tests.

## AI data boundaries

Fleet cases and report data are automatically indexed in `DATA_DIR/fleet_knowledge.sqlite3`. This is historical evidence and requires no per-case approval. Agent-reported AI answers go to AI Review and remain pending until a developer reviews them. Verified manual knowledge remains in the maintenance knowledge library.

The full CSV export includes all case keys currently stored, including report fields and nested report/media metadata (JSON-encoded in CSV cells). Historical CSVs can also be bulk imported from AI Knowledge > Fleet Knowledge.
