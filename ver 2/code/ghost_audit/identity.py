"""Current + historical identity index with exact, normalized matching only."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .evidence.model import norm_email, norm_phone


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

    def resolve_email(self, email: str) -> Subject | None:
        owners = self.key_owner.get(f"email:{norm_email(email)}", set())
        if len(owners) == 1:
            return self.subjects[next(iter(owners))]
        return None

    def owners_of(self, keys: set[str]) -> set[str]:
        """Subjects that own any of the given strong keys (ignores weak name keys)."""
        out = set()
        for k in keys:
            if not k.startswith("name:"):
                out |= self.key_owner.get(k, set())
        return out
