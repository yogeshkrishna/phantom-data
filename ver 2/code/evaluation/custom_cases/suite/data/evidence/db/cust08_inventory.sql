BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE backup_restore_entries (resource_id TEXT PRIMARY KEY, customer_reference TEXT, customer_email TEXT, archive_key TEXT NOT NULL, restore_allowed INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE security_audit_records (resource_id TEXT PRIMARY KEY, subject_reference TEXT NOT NULL, event_code TEXT NOT NULL, retention_until TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO snapshot_scope VALUES ('backup_service', '2026-07-30T10:00:00Z');
INSERT INTO snapshot_scope VALUES ('security_service', '2026-07-30T10:00:00Z');
INSERT INTO backup_restore_entries VALUES ('backup_9801', 'user_914', NULL, 'archive/customer-data/backup_9801.tar.zst', '1', '2026-07-30T09:40:00Z');
INSERT INTO security_audit_records VALUES ('audit_record_9801', 'user_914', 'ACCOUNT_DELETION_RECORDED', '2031-12-31T23:59:59Z', '2026-07-30T09:30:00Z');
COMMIT;
