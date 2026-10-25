-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-05-30T11:00:00Z
-- Database topology: read_replica
BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_primary" VALUES('audit_record_9301','security_service','security_audit_events','{"resource_id": "audit_record_9301", "subject_reference": "user_903", "event_name": "account_deletion_reviewed", "actor_type": "automated_privacy_workflow"}','2026-05-30T11:00:00Z');
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT NOT NULL, collection_name TEXT NOT NULL, record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "customer_data_read_replica" VALUES('account_9301','account_service','customer_profiles','{"resource_id": "account_9301", "customer_reference": "user_903", "full_name": "Lena Roos", "email": "lena.roos@example.org", "phone": "+31 6 111 903", "account_status": "ACTIVE", "signup_channel": "organic_search"}','2026-05-30T11:00:00Z');
INSERT INTO "customer_data_read_replica" VALUES('audit_record_9301','security_service','security_audit_events','{"resource_id": "audit_record_9301", "subject_reference": "user_903", "event_name": "account_deletion_reviewed", "actor_type": "automated_privacy_workflow"}','2026-05-30T11:00:00Z');
COMMIT;
