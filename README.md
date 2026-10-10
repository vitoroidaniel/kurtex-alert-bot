# Kurtex Alert Bot - dashboard update

This package continues from `kurtex-alert-bot-dashboard-refactor.zip`. Telegram
commands, assignments, scheduled reports and configuration continue to use the
existing bot. Case Workspace now manages reported maintenance cases in the same
case store: create a report, edit its description/location/priority, add notes,
or confirm Close case. Workspace shows active reports only; closing marks Done
and preserves the record in the separate Cases history. Workspace writes do not
send Telegram messages. Bot report saves and dashboard writes share the store
lock, and a report arriving during closure cannot reopen the case.

Agents can update reports assigned to them; developers and super admins can
update all reports. Writes recheck account membership and Cases permission,
require a session CSRF token, and reject stale edits. Failed saves retain the
form. This update was source-reviewed only; no local tests or app startup were
run, as requested.

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


## v124 AI chat context
AI Assistant supports per-chat file attachments and verified Knowledge Library sources. PDF/DOCX extraction requires the dependencies listed in requirements.txt.

## Fleet knowledge and AI review
Resolved/reported case records are automatically indexed into `fleet_knowledge.sqlite3` on the persistent data volume. This evidence is retrieved for similar maintenance questions without creating thousands of manual approval tasks. AI Review is reserved for answers explicitly reported by agents. The dashboard CSV export is full-fidelity and includes report fields stored on each case, including nested report/media metadata as JSON cells.

## v7 settings UX
- Theme control moved into Settings; removed from sidebar/mobile quick menu.
- Desktop sidebar pinning now uses a persistent user preference with fixed viewport positioning and correct main-content offset.
- Settings reorganized into Overview, Appearance, Interface, AI Assistant, plus Developer for developer-role users only.
- Workers AI connection diagnostics moved out of AI Knowledge and into Developer settings.
- Added Remember last page and Default landing page preferences.
- Sign out now uses a dedicated confirmation dialog.

- v68: Developer workspace rearranged into Connections & Bot Health header, Manage Users main panel, and compact System Status side panel.

## v74 compact sidebar fix
- Compact desktop navigation keeps every real destination visible, including items that normally live inside Analytics and Fleet groups.
- The center navigation rail scrolls independently on shorter screens while logo and account controls stay fixed.
- Full icon rows are clickable and keyboard accessible; hover tooltips expose destination names.


## v97
Leaderboard upgraded into a team performance workspace with real case metrics, agent ranking, trend/outcome charts, and deterministic Kurtex performance insights. No response-time scoring is used.

### v102
- Leaderboard charts update in place with animation disabled for invisible background refreshes.
- Resolved, Active, and Reassigned now use distinct colors.
- Agent ranking has a working metric filter and 5-agent pagination with Previous/Next controls.
- Chicago time card is hidden on Developer and Agents workspaces.
- Developer workspace cards were reorganized into compact service health, overview/runtime, connection tests, live activity, and health checks.

### Telegram Mini App report form
Report actions can open a Kurtex-styled Telegram Mini App instead of the long step-by-step chat form. Set `KURTEX_PUBLIC_URL` (recommended) to the HTTPS public dashboard origin, e.g. `https://your-service.up.railway.app`. If omitted, `RAILWAY_PUBLIC_DOMAIN` is used automatically when available. Mini App submissions verify Telegram `initData`, require an authorized Kurtex user, send the report/attachments to `REPORTS_GROUP_ID`, update the linked case, and clear the form after a successful send. The classic chat report flow remains as a fallback when no public URL is available.
