"""CSV loading and timestamp helpers."""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from .candidates import enabled

_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?")


def parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime (None when absent)."""
    if not value:
        return None
    m = _ISO_RE.search(str(value))
    if not m:
        return None
    if enabled("fractional_timestamps"):
        # fromisoformat retains microseconds and normalizes offsets without a
        # floating-point timestamp round trip. Naive inputs retain legacy UTC.
        value = m.group(0)
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    date, clock, tz = m.groups()
    dt = datetime.strptime(f"{date}T{clock}", "%Y-%m-%dT%H:%M:%S")
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
        return datetime.fromtimestamp(dt.replace(tzinfo=timezone.utc).timestamp() - offset, tz=timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


class Dataset:
    """All participant-facing tabular inputs, loaded once."""

    def __init__(self, data_dir: Path, requests_file: str = "deletion_requests.csv"):
        self.data_dir = Path(data_dir)
        self.requests = read_csv(self.data_dir / requests_file)
        self.identities = read_csv(self.data_dir / "user_identities.csv")
        self.audit_log = read_csv(self.data_dir / "user_audit_log.csv")
        self.activity = read_csv(self.data_dir / "system_activity.csv")
        self.rules = read_csv(self.data_dir / "deletion_rules.csv")
        self.manifest = read_csv(self.data_dir / "evidence_files.csv")
        self.extra_requests: list[dict[str, str]] = []
        # Other participant request files share the global activity stream; they help
        # disambiguate workflow events but never receive predictions here.
        for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
            path = self.data_dir / name
            if name != requests_file and path.exists():
                self.extra_requests.extend(read_csv(path))

    def all_requests(self) -> list[dict[str, str]]:
        seen = {r["case_id"] for r in self.requests}
        return self.requests + [r for r in self.extra_requests if r["case_id"] not in seen]
