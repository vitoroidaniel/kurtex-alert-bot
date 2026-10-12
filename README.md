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

## v147 - Task-focused multilingual AI and video understanding

**AI backend only.** Telegram Report, Close Case, part-number questions, and all Telegram handlers remain exactly as in v146. Deploy this ZIP to the **existing** `kurtex-bot` service (do not create a second Telegram polling instance).

- The existing Cloudflare Workers AI model is retained; there is no separate self-training model or new secret. Task routing selects concise answers for maintenance diagnosis, fault codes, historical cases, part identification and visual/video inspection.
- Replies follow the agent's request language (English, Romanian, Russian or Spanish) when recognizable, including multilingual maintenance vocabulary expansion when searching English fleet records. A short ambiguous request may default to English. This is prompt-level behavior, not a guarantee of translation accuracy.
- Video uploads (MP4, MOV, WEBM; up to 80 MB and 2 minutes) are decoded server-side with `imageio-ffmpeg` into **8 timestamped still frames**. Frames go to the existing vision-capable model, with speech transcribed separately through Cloudflare Whisper. No continuous-motion tracking or audio-acoustic diagnosis is claimed. An image/frame observation must not be presented as something the driver said, or vice versa.
- Upload JSON and chat attachment metadata now include `processing_status`, `frames_sampled`, `frame_timestamps`, `duration`, `audio_status`, `audio_language` (when the provider supplies it), and `warning` (if audio fails but frames succeed). If the speech provider is unavailable, visual analysis remains usable with an explicit warning.
- Chat responses now include the detected `language`, `task` and `video_frames_reviewed`. Video timestamps and transcription are passed to the model as separate evidence. Existing conversation and case schemas remain compatible.
- Cloudflare configuration: keep `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `KURTEX_AI_MODEL` unchanged. The optional `KURTEX_SPEECH_MODEL` defaults to `@cf/openai/whisper-large-v3-turbo`. Live speech accuracy, model vision capability and costs depend on Cloudflare service configuration.
- No automatic retraining or storing private video data with external search providers is added. Media still uses the existing bot AI chat store and its persistent `DATA_DIR` volume. Keep your existing data volume attached.

**Verification:** `PYTHONPATH=. python -m unittest discover -s tests -p test_v147_ai.py` runs nine offline tests, including real FFmpeg frame extraction, mock multilingual speech transcription, language detection, task selection, and cross-language retrieval. Actual Cloudflare inference and Railway integration require post-deploy testing.

**Deploy order:** bot v147 first, then Desktop v147 and Mobile v147. Older Desktop/Mobile versions can still use the bot's existing AI endpoints.

## v148 - Case Workspace search and attachment preview API

- Expands existing read-only `/api/cases?filter=all&search=` to include unit number, maintenance issue, reported driver, comments and workspace note text (including multilingual casefold matching).
- Returns original Telegram report media as attachment metadata (`name`, `type`, `index`, `previewable`) without exposing Telegram file IDs.
- Adds authenticated, read-only `/api/cases/<case_id>/media/<index>` proxy to retrieve Telegram photos, videos and documents from the existing BOT_TOKEN, with a 20 MB maximum, timeout, MIME filtering and sanitized file paths.
- Allows users who have Cases access to **read** managed workspace attachments in Case View and Report View. Existing upload/delete permissions and CSRF protections are unchanged.
- No changes to Telegram Report, Close Case, part-number capture, AI model or message handlers.

Deploy this bot ZIP before Desktop v148 for full historical search and original Telegram attachment previews. Preserve all existing Railway variables and persistent volumes. Telegram media may be unavailable if Telegram no longer provides the file. No new environment variables are required.

Validation: Python syntax and isolated unit tests for media metadata, bounded proxy retrieval and missing attachment handling passed. Live Telegram network calls and Railway authentication have not been tested.


## v149 — Load type choices

Telegram chat-based Report and Edit Report now offer JBS Load, Broker Load, Meijer Load, OpenDeck and Empty. Meijer Load and OpenDeck follow the existing pickup/delivery flow. Empty continues to skip pickup/delivery. The legacy HTML form's dropdown has the same choices. No desktop or mobile changes.
