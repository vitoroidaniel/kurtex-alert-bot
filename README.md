# Kurtex Bot + API

This repository contains **Telegram bot logic and the shared API only**. It does **not** contain the desktop dashboard or mobile PWA. It runs as **one Railway service** with the existing persistent data volume.

## Structure

- `bot.py` - Telegram polling, commands, notifications and heartbeat
- `bot_app/handlers/` - Telegram handlers and scheduler
- `api/server.py` - shared HTTPS API, Telegram authentication, SSO and reporting
- `backend/storage/` - existing case, user and preference persistence
- `backend/ai/` - AI and maintenance intelligence
- `backend/core/` - time, configuration, case snapshot and shift logic
- `templates/telegram_login.html` - Telegram SSO login page (required by the API)
- `templates/telegram_report.html` - legacy Mini App page retained for compatibility; the Telegram Report button now uses the chat flow
- `static/css/auth/`, `static/images/` - **only** assets for the Telegram sign-in form
- `data/parts_manual_ai.json` - AI reference data (not runtime case records)

`dashboard.py` was renamed to `api/server.py`, because it contains essential API endpoints, not desktop UI. `backend/core/dashboard_data.py` was renamed to `backend/core/case_snapshot.py`. The bot imports `start_api_thread` and still starts the API alongside Telegram polling.

## Railway

Use the **existing bot Railway service and existing volume**, not a new polling instance. Start command: `python bot.py`.

Preserve all existing Railway secrets and `DATA_DIR`, especially `BOT_TOKEN` and `DASHBOARD_SECRET`. `API_SESSION_SECRET` is a new optional alias for fresh installs; **do not add a new value on the existing service**, as doing so would rotate the session signing key and log users out. The legacy secret and preference file names remain readable to avoid data loss.

Set `DESKTOP_PUBLIC_URL` and `MOBILE_PUBLIC_URL` to the **same public Kurtex gateway origin** for SSO callbacks. `KURTEX_PUBLIC_URL` may remain configured for existing API links; it no longer switches the Telegram Report button into a Mini App.

The existing Telegram commands, API endpoints and case history remain available. This release restores the chat-based Report flow and adds optional part-number evidence when a case is closed. The Mini App API route remains in place for backward compatibility, but the bot no longer launches it.

**Never deploy a second Telegram polling process with the same token.** See the migration guide before changing Railway services.

## v146 - Telegram report + replaced part numbers

- **Report** stays inside the Telegram chat. The bot never opens the Mini App/modal from the Report button, even if `KURTEX_PUBLIC_URL` is set.
- **Solve → Close** asks whether any parts were replaced **before** saving the closed status.
- **No** closes with `parts_replaced: false` and `replaced_parts: []`.
- **Yes** asks for actual part numbers in the chat (up to 20, comma/newline separated), then closes and saves them in `replaced_parts` and `replaced_part_numbers`.
- **Part # unavailable** closes with `parts_replaced: true` but no invented part number. **Cancel** leaves the case active.
- The same JSON case record powers the existing Desktop/Mobile read-only API and the AI's fleet history. The AI can search recorded part numbers and must not treat a replaced part as proof of a successful repair.
- No database migration is needed; the new fields are optional. Existing case records remain unchanged.

To run offline regression tests (no Telegram network or token needed): `python tests/test_v146.py`.

**Deployment:** Replace the code in the **existing** `kurtex-bot` service, keep its Railway volume and environment variables. Do not start another bot process with the same token. Desktop and Mobile deployments are not required for this update.
