# SQLite recovery runbook

Use a single backend instance with a durable mounted DATA_DIR. Never mount production data into staging.

1. Run python scripts/audit_sqlite.py against a copy of the database.
2. Run python scripts/backup_sqlite.py SOURCE DESTINATION to obtain a consistent online database snapshot. Repeat for each SQLite database, including AI and Smart Lists.
3. Copy attachments and media to separately protected storage. Database snapshots alone do not back up files. Quiesce writes if a consistent database/media recovery point is required.
4. Restore only into a new isolated directory. Run the audit and integration tests there before any cutover.
5. Never overwrite live files during rollback. Stop writers, retain the failed state, and use a verified snapshot only with explicit operator approval.

The backup command is not a scheduled job. Configure a protected off-volume backup destination and scheduler in staging before activating it. Keep secrets out of backups and logs.
