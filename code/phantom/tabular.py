"""tabular.py – load the five core CSV tables through the reviewed CSV reader."""

from pathlib import Path

from .corpus import read_csv_table

TABLE_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "deletion_requests",
        (
            "case_id", "email", "requested_at", "reported_status",
            "reported_at", "audit_at", "request_text",
        ),
        "case_id",
    ),
    (
        "user_identities",
        ("user_id", "name", "email", "phone", "email_hash", "updated_at"),
        "user_id",
    ),
    (
        "user_audit_log",
        (
            "audit_id", "user_id", "event_time",
            "field_name", "old_value", "new_value",
        ),
        "audit_id",
    ),
    (
        "system_activity",
        (
            "activity_id", "event_time", "event_type", "source_system",
            "target_system", "resource_id", "reported_result", "details",
        ),
        "activity_id",
    ),
    (
        "deletion_rules",
        ("rule_id", "data_category", "condition", "required_action"),
        "rule_id",
    ),
)


def load_tables(dataset_dir: Path) -> dict[str, tuple[dict[str, str], ...]]:
    """Read all five required CSV tables and return them keyed by table name.

    Each value is a tuple of row dicts in CSV order with exact string values.
    Validation and filesystem errors propagate; no partial results are returned.
    """
    result: dict[str, tuple[dict[str, str], ...]] = {}
    for table_name, columns, id_column in TABLE_SPECS:
        path = dataset_dir / (table_name + ".csv")
        rows = read_csv_table(path, columns, id_column)
        result[table_name] = tuple(rows)
    return result
