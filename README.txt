KURTEX V10 SHARED CARD ORDER — TWO PATCHES

Desktop: Copy static/js/features/case-workspace.js into your Desktop project, overwriting V9.
Bot: Copy backend/core/workspace_order.py and apply_bot_order_patch.py into your Bot project.
Then from Bot project root run: python apply_bot_order_patch.py
It backs up api/server.py and inserts one authenticated endpoint.

IMPORTANT: The Bot installer changes api/server.py; do not upload the installer alone expecting it to work.
No Telegram command or case storage code is changed. New order is stored in DATA_DIR/workspace_card_order.json.
The API uses a version number to reject simultaneous stale ordering writes.
Agents should reload Desktop after backend deploy. Ordering refresh follows existing board polling (up to ~15 sec).

This package is NOT tested end-to-end on the deployed server. Back up first and test in staging.
