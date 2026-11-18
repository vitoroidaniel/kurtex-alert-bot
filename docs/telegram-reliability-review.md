# Telegram reliability review — 2026-11-18

Scope: existing Telegram reporting, commands, alerts, storage access, networking,
and lifecycle. No dashboard/mobile UI or AI behavior changes.

## Incident evidence

Production logs show repeated Telegram `getUpdates` Bad Gateway failures between
16:55 and 17:00 UTC, after the Reefer/Empty report reached Current Location at
16:53. Receiving updates recovered by 17:01. The location text was not logged,
so the logs cannot establish whether that specific reply reached its handler.
The Current Location handler stores a field in memory and sends Comments; it
does not query SQLite. CPU/memory saturation and SQLite-lock errors were not
observed in the sampled incident logs. JSON/SQLite is therefore not established
as the cause of this incident.

## Internal reliability changes

- Keep each user's report steps ordered while processing different users in
  parallel. Group alerts have four separate handler slots, leaving sixteen
  slots for private interactions. Admission remains bounded at 256 updates.
- Move user/permission reads and all session persistence I/O off the shared
  asyncio loop. Serialize persistence workers, including cancellation cleanup.
- Preserve PTB's existing pickle format and write session snapshots through a
  temporary file, file sync, and atomic replacement. A failed replacement leaves
  the previous snapshot intact.
- Give bulk notifications, reminders, broadcast recipients, and cleanup their
  own eight-connection pool, separate from interactive report requests and
  getUpdates. Bulk requests cannot consume interactive connections and are paced
  at no more than ten dispatches per second to reduce flood-limit pressure.
- Bound callback acknowledgements at 1.5 seconds. Lower connection/read/pool
  timeouts so dead connections reach retries sooner. Retain pending Telegram
  updates and existing drafts. Honor Telegram's full RetryAfter duration.
- Persist a known report-delivery marker before saving its case. Retrying a DB
  save reuses that marker instead of posting the same known-delivered report.
  A failed private confirmation edit does not turn a saved report into a failed
  send. Failed sends/saves retain the report draft and existing retry controls.
- Retry transient network failures during Application initialization with
  backoff capped at 30 seconds. Invalid tokens and non-network faults still fail
  visibly. Optional command-menu refresh runs separately from polling startup.
- Track and cancel lifecycle tasks explicitly; drain assignment background tasks
  before closing Telegram clients. Log slow updates, queued work, report-location
  entry, and network retry attempts without logging report text.
- A separate watchdog thread detects an event loop that cannot pulse for 120
  seconds, prints thread stacks, and exits with code 1 for Railway's existing
  restart policy. Async network waits leave pulses running and do not trigger it.

## Validation

43 related tests passed against the current repository baseline. Tests include:

- 100 simulated Current Location replies while a DB lookup and session write
  remain deliberately stalled; all advance to Comments.
- 64 stalled bulk requests and 64 group updates while interactive work proceeds.
- Fast replies during a stuck button acknowledgement; same-user ordering and
  duplicate Send clicks; cancellation/exception cleanup.
- Existing session-format compatibility, concurrent session updates, interrupted
  atomic writes, and on-disk delivery checkpoints before a failed case save.
- Successful delivery followed by a DB failure or private confirmation failure.
- Slow optional command registration, transient startup errors, invalid tokens,
  genuine event-loop stalls, and slow async waits that must not trip the watchdog.
- Existing related case, permission, history, and feedback regressions.

Telegram calls and watchdog termination are mocked in tests. These checks verify
logic and isolation, not Telegram's production throughput or uptime. No test
messages or fake cases were sent to production users.

## Remaining operational limits

- Telegram/API/network outages can still delay incoming and outgoing messages.
  A process restart does not repair an upstream outage.
- Telegram sendMessage has no application idempotency key. A timeout after remote
  delivery but before receiving its response remains ambiguous; the delivery
  marker protects acknowledged sends, not this unavoidable uncertainty window.
- Drafts are saved every 30 seconds and on graceful shutdown. Abrupt process or
  volume failure can lose the latest unsaved draft edits. Atomic replacement
  protects the previous snapshot; it is not an off-volume backup.
- The service is a single polling replica, guarded by the existing volume lock.
  Work queues and pools have finite capacity; arbitrary overload cannot have
  zero queueing delay. Do not add polling replicas for the same bot token.
- Railway remains configured for ON_FAILURE with 10 retries. The startup retry
  loop avoids consuming those retries for ordinary Telegram network failures;
  repeated genuine crashes still require investigation. Railway deployment
  healthchecks are not continuous uptime monitoring. Backup/restore drills and
  external continuous monitoring were not validated by this code review.

Useful sources: [PTB conversation concurrency](https://docs.python-telegram-bot.org/en/v21.6/telegram.ext.conversationhandler.html),
[Railway restart policy](https://docs.railway.com/deployments/restart-policy),
[Railway healthcheck scope](https://docs.railway.com/deployments/healthchecks).
