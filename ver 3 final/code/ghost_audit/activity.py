"""Index of the global system_activity stream.

Activity rows are *claims*: they never prove a resource's final state on their own. They are
used for (a) explicit catalog links between a resource and a subject, (b) placing deletion-
workflow events next to the request they were reported for, and (c) recreation signals.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .evidence.model import EMAIL_RE, USER_RE, keys_from_value, system_for_resource
from .loaders import parse_ts

LINK_RE = re.compile(r"linked (\S+) \(([^)]*)\) to (\S+?) and (user_\d+)")
ATTACH_LINK_RE = re.compile(r"confirmed (\S+) \(([^)]*)\) belongs only to (\S+?) and (user_\d+)")
REPOPULATE_RE = re.compile(r"repopulated downstream resource (\S+?)\.?$")


@dataclass
class Activity:
    activity_id: str
    time: object
    event_type: str
    source: str
    target: str
    resource_id: str
    result: str
    details: str

    @property
    def system(self) -> str:
        return system_for_resource(self.resource_id) or self.target


@dataclass
class CatalogLink:
    activity_id: str
    time: object
    resource_id: str
    display_ref: str
    keys: set
    exclusive: bool


class ActivityIndex:
    def __init__(self, rows: list[dict]):
        self.rows = [Activity(r["activity_id"], parse_ts(r["event_time"]), r["event_type"], r["source_system"],
                              r["target_system"], r["resource_id"], r["reported_result"], r["details"])
                     for r in rows]
        self.by_id = {a.activity_id: a for a in self.rows}
        self.by_resource = defaultdict(list)
        for a in self.rows:
            self.by_resource[a.resource_id].append(a)
        self.links: list[CatalogLink] = []
        self.non_deletion_accounts: dict[str, Activity] = {}
        self.repopulations: list[tuple[Activity, str]] = []
        self.workflow: list[Activity] = []
        for a in self.rows:
            if a.event_type == "LINK_RESOURCE_IDENTITY":
                m = LINK_RE.search(a.details) or ATTACH_LINK_RE.search(a.details)
                if m:
                    rid, display, email, user = m.groups()
                    keys = keys_from_value(email) | keys_from_value(user)
                    self.links.append(CatalogLink(a.activity_id, a.time, rid, display, keys,
                                                  exclusive="belongs only" in a.details))
            elif a.event_type == "CLASSIFY_REQUEST":
                self.non_deletion_accounts[a.resource_id] = a
            m = REPOPULATE_RE.search(a.details)
            if m and ("repopulated" in a.details):
                self.repopulations.append((a, m.group(1)))
            if a.source == "deletion_worker":
                self.workflow.append(a)
        self.workflow.sort(key=lambda a: a.time)

    @staticmethod
    def is_primary_account_submission(a: Activity) -> bool:
        return a.event_type == "DELETE_RECORD" and a.target == "account_service"
