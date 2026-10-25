"""Current + historical identity index with exact, normalized matching only."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .candidates import enabled
from .evidence.model import norm_email, norm_phone
from .loaders import parse_ts


def _known_time(value):
    # Malformed optional metadata cannot manufacture an identifier revision or
    # break the baseline identity parser, which did not interpret these dates.
    try:
        return parse_ts(value)
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class Subject:
    user_id: str
    name: str
    emails: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    current_email: str = ""
    history: list = field(default_factory=list)  # audit ids that changed identifiers

    @property
    def keys(self) -> set[str]:
        """Strong identity keys usable to link evidence to this subject."""
        out = {f"user:{self.user_id.lower()}"}
        out |= {f"email:{e}" for e in self.emails}
        out |= {f"hash:{h}" for h in self.hashes}
        out |= {f"phone:{p}" for p in self.phones}
        return out

    @property
    def name_key(self) -> str:
        return f"name:{' '.join(self.name.lower().split())}"


class IdentityIndex:
    def __init__(self, identities: list[dict], audit_log: list[dict]):
        self._identity_rows = {row["user_id"]: dict(row) for row in identities}
        self._audit_rows = list(audit_log)
        self._audit_by_user = defaultdict(list)
        self._identity_updated = {uid: _known_time(row.get("updated_at")) for uid, row in self._identity_rows.items()}
        for row in audit_log:
            self._audit_by_user[row["user_id"]].append((_known_time(row.get("event_time")), row))
        self.subjects: dict[str, Subject] = {}
        for row in identities:
            s = Subject(user_id=row["user_id"], name=row["name"], current_email=norm_email(row["email"]))
            s.emails.add(norm_email(row["email"]))
            if row.get("email_hash"):
                s.hashes.add(row["email_hash"].strip().lower())
            if row.get("phone"):
                s.phones.add(norm_phone(row["phone"]))
            self.subjects[s.user_id] = s
        for row in audit_log:
            s = self.subjects.get(row["user_id"])
            if s is None:
                continue
            s.history.append(row["audit_id"])
            for value in (row["old_value"], row["new_value"]):
                if not value:
                    continue
                if row["field_name"] == "email":
                    s.emails.add(norm_email(value))
                elif row["field_name"] == "email_hash":
                    s.hashes.add(value.strip().lower())
                elif row["field_name"] == "phone":
                    s.phones.add(norm_phone(value))
        self.key_owner: dict[str, set] = defaultdict(set)
        for s in self.subjects.values():
            for k in s.keys:
                self.key_owner[k].add(s.user_id)

    def subject_at(self, user_id: str, audit_at) -> Subject | None:
        """Historical keys known at cutoff; future revisions are not evidence.

        Without an earlier audited value, a future-updated identity field is
        unknown at the old cutoff. We never reconstruct it from a future event's
        old_value, since that information was not yet available to the audit.
        """
        row = self._identity_rows.get(user_id)
        if row is None:
            return None
        updated = self._identity_updated.get(user_id)
        known_current = updated is None or updated <= audit_at
        subject = Subject(user_id, row.get("name", "") if known_current else "")
        if known_current:
            subject.current_email = norm_email(row.get("email", ""))
            if subject.current_email:
                subject.emails.add(subject.current_email)
            if row.get("email_hash"):
                subject.hashes.add(row["email_hash"].strip().lower())
            if row.get("phone"):
                subject.phones.add(norm_phone(row["phone"]))
        eligible = [(time, r) for time, r in self._audit_by_user.get(user_id, [])
                    if time is not None and time <= audit_at]
        for _, change in sorted(eligible, key=lambda item: (item[0], item[1]["audit_id"])):
            subject.history.append(change["audit_id"])
            for value in (change.get("old_value"), change.get("new_value")):
                if not value:
                    continue
                if change["field_name"] == "email":
                    subject.emails.add(norm_email(value))
                elif change["field_name"] == "email_hash":
                    subject.hashes.add(value.strip().lower())
                elif change["field_name"] == "phone":
                    subject.phones.add(norm_phone(value))
            if change["field_name"] == "email" and change.get("new_value"):
                subject.current_email = norm_email(change["new_value"])
        return subject

    def resolve_email(self, email: str, audit_at=None) -> Subject | None:
        if enabled("temporal_identity") and audit_at is not None:
            key = f"email:{norm_email(email)}"
            subjects = [self.subject_at(uid, audit_at) for uid in self.key_owner.get(key, ())]
            owners = [subject for subject in subjects if key in subject.keys]
            return owners[0] if len(owners) == 1 else None
        owners = self.key_owner.get(f"email:{norm_email(email)}", set())
        if len(owners) == 1:
            return self.subjects[next(iter(owners))]
        return None

    def owners_of(self, keys: set[str], audit_at=None) -> set[str]:
        """Subjects that own any of the given strong keys (ignores weak name keys)."""
        out = set()
        if enabled("temporal_identity") and audit_at is not None:
            strong = {key for key in keys if not key.startswith("name:")}
            possible = set().union(*(self.key_owner.get(key, set()) for key in strong))
            return {uid for uid in possible if self.subject_at(uid, audit_at).keys & strong}
        for k in keys:
            if not k.startswith("name:"):
                out |= self.key_owner.get(k, set())
        return out
