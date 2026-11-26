# Database recovery candidate

The backend currently uses SQLite WAL on a shared persistent volume, not
PostgreSQL. This utility takes an online, transaction-consistent snapshot of
one database via SQLite's backup API. It does not copy the live WAL file and
never overwrites an existing destination.

## Before any release

1. Confirm the actual mounted DATA_DIR and the active backend deployment.
2. Provision a secure independent backup destination (not the same volume).
3. Snapshot each database separately, including kurtex.sqlite3 and the Smart
   Lists database. Save the JSON report and checksum with each snapshot.
4. Verify each checksum and integrity check on the backup host. Restore into
   a fresh isolated directory and exercise application-level read/write tests.
5. Include attachment objects and other durable files in a coordinated backup.
   Separate database snapshots do NOT guarantee cross-database point-in-time
   consistency. Use a controlled quiescence window if that is required.
6. Test recovery time and record the last verified recovery point. Never run
   a destructive restore against a running bot or active Live Board.

Example, with explicitly verified source/destination paths:

    python scripts/sqlite_snapshot.py create /app/data/kurtex.sqlite3 /secure-backups/cases-unique.sqlite3
    python scripts/sqlite_snapshot.py verify /secure-backups/cases-unique.sqlite3 --sha256 EXPECTED_CHECKSUM
    python scripts/sqlite_snapshot.py create /secure-backups/cases-unique.sqlite3 /isolated-restore/cases.sqlite3

The backup files contain private operational data and must be protected with
appropriate access, encryption, retention and offsite storage. These are
operator commands, not an automated production backup job. No production
volume, credentials, or case records are used in the included tests.
