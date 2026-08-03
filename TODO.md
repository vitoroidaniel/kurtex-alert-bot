# OILLOG — architecture notes

## Auth model (updated)

OILLOG no longer manages its own user whitelist. It shares the **same
Telegram login + user database** as the Kurtex Alert Bot:

- `storage/user_store.py` here is a copy of the alert bot's own
  `storage/user_store.py`. Both apps read/write the same `users.json`,
  identified by numeric Telegram ID, with a `role` of `developer`,
  `super_admin`, or `agent`.
- **User management happens only in the alert bot**, via its existing
  Telegram commands: `/adduser <id> <name> <role>`, `/removeuser <id>`,
  `/editrole <id> <role>`, `/listusers`. OILLOG has no whitelist commands
  of its own anymore — nothing to configure here for that.
- Login uses Telegram's **redirect flow** (`data-auth-url`), matching the
  alert bot's own dashboard — not the old popup (`data-onauth`) flow. This
  also sidesteps the popup issue you were hitting.
- `role` decides access: `developer` and `super_admin` can use `/admin`;
  any registered user (including plain `agent`) can use the mobile app `/`.
  Not-yet-registered Telegram accounts get a clear on-screen error instead
  of a silent failure.

### This only works if both apps share the same data volume

`storage/user_store.py` reads `DATA_DIR` (defaults to `/app/data`) to find
`users.json`. For OILLOG to see the *same* users as the bot, this app's
`DATA_DIR` must point at the **same physical volume** the alert bot uses.
On Railway that means either:

- Deploy OILLOG as a second process in the **same Railway service** as the
  bot (simplest — one volume, shared automatically), or
- Attach the **same volume** to both services if your Railway plan/project
  supports mounting one volume across services.

If they end up on two independent, unshared volumes, logins will fail with
"not whitelisted" even for people the bot already knows about — because
this app will be reading an empty/different `users.json`.

OILLOG's *own* data (entries.json, units.json, settings.json) still uses
`OILLOG_DATA_DIR` — that can be the same volume or a different one, it's
unrelated to the user/auth store.

## Env vars

| Variable | Used by | Notes |
|---|---|---|
| `BOT_TOKEN` | both | must be the same bot as the alert bot uses |
| `DATA_DIR` | `storage/user_store.py` | must resolve to the same volume as the alert bot |
| `OILLOG_DATA_DIR` | server.py | OILLOG's own entries/units/settings storage |
| `OILLOG_SECRET` | server.py | Flask session signing key, any random string |

`BOT_USERNAME`, `ADMIN_CHAT_IDS`, `TELEGRAM_WEBHOOK_SECRET` are no longer
used by this app (the bot owns the Telegram webhook and command handling).

## Deployment notes

1. Both the widget in `oillog-mobile.html` and `oillog-admin.html` reference
   the bot username directly (`data-telegram-login="kurtexsecuritybot"`) —
   update this if the shared bot's username changes.
2. In @BotFather, set the domain for the bot (`/setdomain`) to the domain
   this app is served from — required for the Telegram Login Widget to work
   at all, independent of the code.
3. `oillog-server.py` (the older/legacy server variant) is unused by this
   deployment (`server.py` is what Railway runs) and doesn't have any of
   this app's current auth logic — safe to delete once you've confirmed
   you don't need it.
