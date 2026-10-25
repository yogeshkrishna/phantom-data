"""Tokenizer for the seven operational log line formats found in the evidence."""

from __future__ import annotations

import json
import re

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
SYSLOG_RE = re.compile(r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+(.*)$")
PIPE_RE = re.compile(r"^(\w+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.*)$")
BRACKET_RE = re.compile(r"^\[(\w+)\]\s*\[([^\]]+)\]\s*\[([^\]]+)\]\s*([\w.]+):\s*(.*)$")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return value


def parse_kv(text: str) -> dict[str, str]:
    return {k: _unquote(v) for k, v in KV_RE.findall(text)}


def parse_line(line: str) -> dict | None:
    """Return {ts, service, trace_id, event, attrs, format} or None for non-log lines."""
    line = line.rstrip("\r\n")
    m = TS_RE.match(line)
    if not m:
        return None
    ts, rest = m.groups()
    rest = rest.strip()
    rec = {"ts": ts, "service": None, "trace_id": None, "event": None, "attrs": {}, "format": None, "raw": line}

    if rest.startswith("{"):
        try:
            data = json.loads(rest)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            rec["format"] = "json"
            rec["service"] = data.get("service") or data.get("logger")
            rec["trace_id"] = data.get("trace_id")
            rec["event"] = data.get("event")
            rec["attrs"] = {k: ("" if v is None else str(v)) for k, v in data.items()}
            return rec

    sm = SYSLOG_RE.match(rest)
    if sm:
        _host, service, trace, body = sm.groups()
        attrs = parse_kv(body)
        rec.update(format="syslog", service=service, trace_id=trace if trace != "-" else None,
                   event=attrs.get("event"), attrs=attrs)
        return rec

    pm = PIPE_RE.match(rest.lstrip("| ")) if rest.startswith("|") else None
    if pm:
        level, service, trace, event, body = pm.groups()
        attrs = parse_kv(body)
        attrs.setdefault("level", level)
        rec.update(format="pipe", service=service, trace_id=trace if trace.startswith("tr-") else None,
                   event=event, attrs=attrs)
        return rec

    bm = BRACKET_RE.match(rest)
    if bm:
        level, service, trace, event, body = bm.groups()
        attrs = parse_kv(body)
        attrs.setdefault("level", level)
        rec.update(format="bracket", service=service, trace_id=trace if trace.startswith("tr-") else None,
                   event=event, attrs=attrs)
        return rec

    attrs = parse_kv(rest)
    if "message" in attrs and "=" in attrs["message"]:
        # cloud runtime export: requestId=<trace> function=<service> message="k=\"v\" ..."
        inner = parse_kv(attrs["message"])
        rec.update(format="cloud", service=attrs.get("function"),
                   trace_id=attrs.get("requestId") if str(attrs.get("requestId", "")).startswith("tr-") else None,
                   event=inner.get("event"), attrs={**{k: v for k, v in attrs.items() if k != "message"}, **inner})
        return rec

    rec.update(format="kv", service=attrs.get("service"), trace_id=attrs.get("trace_id"),
               event=attrs.get("event"), attrs=attrs)
    return rec
