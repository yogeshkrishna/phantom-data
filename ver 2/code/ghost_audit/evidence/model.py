"""Normalized evidence observations and identity-key extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..config import PREFIX_SYSTEM

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b", re.IGNORECASE)
HASH_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
PHONE_RE = re.compile(r"\+\d[\d\s().\-]{6,}\d")
RESOURCE_RE = re.compile(
    r"\b(account|search|analytics|support|export|data_flow|backup|audit_record|tombstone|cache|"
    r"email_delivery|marketing|legal_hold|object)_(\d+)\b"
)

# Field names whose values are direct identifiers that policy cares about.
PII_FIELDS = {
    "email", "full_name", "name", "phone", "email_hash", "display_name", "owner_email", "owner_phone",
    "requester_email", "requester_name", "requester_phone", "customer_email", "message_text",
    "matched_value",
}
# Field names that carry a stable subject reference (allowed on tombstones / retained records).
REFERENCE_FIELDS = {"customer_reference", "subject_reference", "user_id", "subject_selector", "subject_ref"}
# Field names that are lookup keys used to find a record (link the record, not PII of the record).
LOOKUP_FIELDS = {"lookup_key", "correlation_key", "subject_key", "match_key", "scan_key", "query"}

REDACTED_MARKERS = {"", "null", "none", "[redacted]", "redacted", "-", "—", "no direct identifier"}


def norm_email(value: str) -> str:
    return value.strip().lower()


def norm_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def keys_from_value(value, field_name: str | None = None) -> set[str]:
    """Identity keys (``kind:value``) found in a raw value."""
    if value is None:
        return set()
    text = str(value)
    if text.strip().lower() in REDACTED_MARKERS:
        return set()
    keys = {f"email:{norm_email(m)}" for m in EMAIL_RE.findall(text)}
    keys |= {f"user:{m.lower()}" for m in USER_RE.findall(text)}
    keys |= {f"hash:{m.lower()}" for m in HASH_RE.findall(text)}
    phones = PHONE_RE.findall(text)
    if not phones and field_name and "phone" in field_name:
        digits = norm_phone(text)
        if len(digits) >= 8:
            phones = [text]
    for p in phones:
        digits = norm_phone(p)
        if len(digits) >= 8:
            keys.add(f"phone:{digits}")
    if field_name in ("full_name", "name", "display_name", "requester_name") and not keys:
        keys.add(f"name:{' '.join(text.lower().split())}")
    return keys


def is_blank(value) -> bool:
    return value is None or str(value).strip().lower() in REDACTED_MARKERS


def system_for_resource(resource_id: str) -> str | None:
    m = RESOURCE_RE.fullmatch(resource_id or "")
    if not m:
        return None
    return PREFIX_SYSTEM.get(m.group(1))


@dataclass
class Observation:
    """One statement an evidence item makes about one resource at one time."""

    file_id: str
    captured_at: datetime  # manifest capture time of the file (citation eligibility)
    obs_time: datetime  # time the statement refers to (state reconstruction)
    system: str
    resource_id: str
    present: bool | None  # True = record exists, False = record absent, None = unknown
    store: str = ""  # table / collection / log source
    replica: bool = False  # observation comes from a replica copy (drift candidate)
    pii: dict = field(default_factory=dict)  # direct identifiers still present on the record
    refs: dict = field(default_factory=dict)  # stable references present on the record
    link_keys: set = field(default_factory=set)  # identity keys tying the record to a subject
    record_type: str | None = None  # data category hint (USER_PROFILE, SUPPORT_ATTACHMENT, ...)
    status: str | None = None  # reported state word (FOUND, ANONYMIZED, ACTIVE, ...)
    flags: dict = field(default_factory=dict)
    kind: str = ""  # parser-specific statement kind (e.g. log event name)
    position: int = -1  # row position inside its collection (for snapshot-order alignment)
    collection: str = ""  # file_id + store, groups rows listed together
    explicit: bool = True  # False when absence is inferred from a captured scope

    @property
    def has_pii(self) -> bool:
        return any(not is_blank(v) for v in self.pii.values())


@dataclass
class Scope:
    """A captured system/collection and the records it listed as present.

    kind="snapshot": a point-in-time capture of a store (SQL tables, bundle collections); a record
    it does not list was absent at capture time. kind="query": a set of point lookups (service
    exports); a record that was never looked up says nothing, so it cannot prove absence.
    """

    file_id: str
    captured_at: datetime
    obs_time: datetime
    system: str
    store: str
    listed: set = field(default_factory=set)
    kind: str = "snapshot"


@dataclass
class EvidenceDoc:
    """A free-form document (PDF/image/text) with the references it mentions."""

    file_id: str
    captured_at: datetime
    text: str
    keys: set = field(default_factory=set)
    resources: set = field(default_factory=set)
