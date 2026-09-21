# Kurtex Alert Bot - dashboard update

This package continues from `kurtex-alert-bot-dashboard-refactor.zip`. Telegram
commands, assignments, report submission, scheduled reports, configuration, and
the bot's case/user stores are unchanged. The dashboard is read-only: alerts and
case actions remain in Telegram. No AI case persistence was added.

## Dashboard structure

- `dashboard.py`: Flask routes, authentication, statistics, and CSV export.
- `dashboard_data.py`: thread-safe, read-only JSON snapshot cache.
- `templates/`: dashboard and sign-in HTML, including shared dialog markup.
- `static/css/`: dashboard, login, and locally served icon styles.
- `static/js/`: request handling, navigation, views, dialogs, reports, charts,
  and refresh scheduling in separate files.
- `static/images/`: one shared logo, replacing repeated embedded image data.
- `static/vendor/`: pinned Chart.js 4.4.0 and its MIT license. No frontend build
  or Node.js installation is required to run the bot.
- `tests/test_dashboard.py`: offline API regression tests with synthetic data.

## Fixes

- Case lists refresh every 30 seconds while visible. Polling pauses in hidden
  tabs, during text input, and while dialogs are open. Loaded pages and filters
  survive background refreshes.
- Requests time out after 12 seconds. Superseded requests cannot replace newer
  searches. Pagination advances only after a successful response.
- Failed requests show a warning instead of silently leaving a spinner. Expired
  sessions return to sign-in. Blocked browser storage no longer prevents boot.
- Unchanged JSON is parsed once. Read failures preserve the last valid snapshot
  with an explicit stale-data warning. Without a valid snapshot, the API returns
  HTTP 503; it does not pretend that case history is empty. Retries are limited
  to one read attempt every two seconds during failures. The cache is in memory
  only and never writes case data.
- Zero-second response times count correctly; nullable legacy fields do not
  break fleet statistics. Trend calculations group cases by day once, and
  periods are bounded to 1-366 days.
- Agent IDs take precedence over matching names. Case detail lookup rejects
  ambiguous ID prefixes. Case reports include the stored location.
- Fleet history distinguishes vehicle types sharing a unit number. An older
  unresolved issue keeps a unit active even if a newer case is resolved.
- Dashboard report resolution rates use resolved cases. Date ranges are checked;
  case-report printing isolates the report. CSV formula-like text is escaped.
- Fleet search fields retain focus while typing. Repeated dialog opens do not
  leave the page scroll-locked; Escape and keyboard navigation are supported.
- Updated light/dark styling, mobile layout, and login screen. Icons, chart
  scripts, and the logo load locally. Telegram sign-in still requires Telegram.

## Run and update

Keep the existing persistent volume and environment settings. Replace the code
package, including the entire `templates/` and `static/` directories together.
Do not replace or delete volume data when updating code.

```sh
python -m pip install -r requirements.txt
python bot.py
```

The existing bot entry point still starts the dashboard thread. Its port remains
`DASHBOARD_PORT` (default `8080`), and `DATA_DIR` still defaults to `/app/data`.
`BOT_USERNAME` is required to display Telegram sign-in. The existing
`DASHBOARD_SECRET` environment value or volume-backed `dashboard_secret` remains
the session signing secret. User roles remain in the existing volume user store.

For offline API tests:

```sh
python -m unittest discover -s tests -v
```

Validation performed: 14 API regression tests; headless Chromium checks for all
dashboard pages, pagination failure/retry, new-case polling, stale search
responses, modal scroll recovery, report print isolation, charts, fleet searches,
report date validation, mobile navigation, request timeouts, and blocked storage.
Tests use sample data and do not call Telegram. The package has not been deployed
or tested against live case intake.

## Cleanup

Removed the unreferenced `user_tracker.py`, the unused login carousel script, and
the old monolithic frontend files after moving their active code. Replaced
repeated base64 logo strings with one image asset. Generated caches, local test
data, screenshots, and development tooling are excluded from the ZIP. Bot
runtime files and all volume history are preserved.
