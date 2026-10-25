"""Load every manifest file once and index the resulting observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from ..loaders import parse_ts
from .parsers import parse_file


class EvidenceIndex:
    def __init__(self, data_dir: Path, manifest: list[dict]):
        self.data_dir = Path(data_dir)
        self.files = {m["file_id"]: m for m in manifest}
        self.captured = {m["file_id"]: parse_ts(m["captured_at"]) for m in manifest}
        self.observations = []
        self.scopes = []
        self.docs = []
        self.coverage = []
        for m in manifest:
            path = self.data_dir / m["file_path"]
            entry = {"file_id": m["file_id"], "path": m["file_path"], "type": path.suffix.lower().lstrip(".")}
            if not path.exists():
                entry.update(status="missing", method="-", observations=0, notes="file not found")
                self.coverage.append(entry)
                continue
            try:
                res = parse_file(path, m["file_id"], self.captured[m["file_id"]])
            except Exception as exc:  # never silently skip: record the failure
                entry.update(status="failed", method="-", observations=0, notes=f"{type(exc).__name__}: {exc}")
                self.coverage.append(entry)
                continue
            self.observations.extend(res.observations)
            self.scopes.extend(res.scopes)
            self.docs.extend(res.docs)
            text_len = sum(len(d.text) for d in res.docs)
            ok = bool(res.observations) or bool(res.scopes) or text_len > 0
            entry.update(status="ok" if ok and not res.notes else ("partial" if ok else "failed"),
                         method=res.method, observations=len(res.observations), doc_chars=text_len,
                         notes="; ".join(res.notes[:3]))
            self.coverage.append(entry)
        self.by_resource = defaultdict(list)
        for obs in self.observations:
            self.by_resource[obs.resource_id].append(obs)
        for lst in self.by_resource.values():
            lst.sort(key=lambda o: (o.obs_time, o.captured_at, o.file_id, o.position))

    def coverage_summary(self) -> dict:
        by_type = Counter(e["type"] for e in self.coverage)
        ok = Counter(e["type"] for e in self.coverage if e["status"] == "ok")
        bad = [e for e in self.coverage if e["status"] != "ok"]
        return {"files": len(self.coverage), "by_type": dict(by_type), "ok_by_type": dict(ok),
                "problems": bad, "observations": len(self.observations), "scopes": len(self.scopes),
                "documents": len(self.docs)}
