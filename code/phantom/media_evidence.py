"""Grounded media layouts. Geometry and labels, never file names or answer tables."""
import hashlib
import re
from dataclasses import replace
from datetime import datetime, timezone

from .contracts import EntityKey, EvidenceFact, EvidenceRef, parse_instant
from .json_evidence import EvidenceBatch, EvidenceDiagnostic
from .local_model import TextSpan

EMAIL = r"[\w.+-]+@[\w.-]+\.[\w-]+"
ID = r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+"


def _date(text):
    value = re.sub(r"(\d{2}) (\d{2}) UTC$", r"\1:\2 UTC", " ".join(text.split()))
    for pattern in ("%d %b %Y %H:%M:%S UTC", "%d %b %Y %H:%M UTC", "%d %b %Y"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    raise ValueError("unsupported displayed timestamp")


def _source(entry):
    return EvidenceRef(entry.file_id, entry.file_path, entry.sha256, "", entry.captured_at)


def _fact(source, system, resource, category, fields, *, observed=None, state="PRESENT", metadata=None, queries=None):
    return EvidenceFact(EntityKey(system, system, resource), category, observed or source.available_at, "state", state,
                        fields, queries or {}, tuple(sorted(fields)), (), metadata or {}, source)


def _bind(fact, context):
    """Use explicit catalog aliases, carrying their later availability and provenance."""
    aliases = {fact.entity.resource_id, *fact.metadata.get("resource_aliases", ())}
    matches = [f for f in context if f.kind == "identity" and f.entity.system_id == fact.entity.system_id
               and aliases.intersection(f.metadata.get("resource_aliases", ()))]
    owners = {(f.entity, f.source.available_at) for f in matches}
    if len({entity for entity, _ in owners}) == 1:
        support = min(matches, key=lambda f: parse_instant(f.source.available_at))
        if support.entity == fact.entity:
            return fact  # The visible record already supplies this exact key.
        available = max((fact.source.available_at, support.source.available_at), key=parse_instant)
        return replace(fact, entity=support.entity, source=replace(fact.source, available_at=available),
                       metadata={**fact.metadata, "binding_evidence_id": support.source.evidence_id,
                                 "source_available_at": fact.source.available_at})
    return fact


def _rows(spans, labels):
    """Find a real table header row, then assign OCR cells using column anchors."""
    candidates = []
    for span in spans:
        if span.text.casefold() == labels[0].casefold():
            header = []
            for label in labels:
                options = [s for s in spans if s.text.casefold() == label.casefold() and abs(s.box[1]-span.box[1]) < 12]
                if options:
                    header.append(min(options, key=lambda s: abs(s.box[1]-span.box[1])))
            if len(header) == len(labels):
                candidates.append(header)
    if not candidates:
        raise ValueError("table header unreadable")
    header = candidates[0]
    anchors = [s.box[0] for s in header]
    right = next((s.box[0] for s in spans if s.text in {"Activity", "History"} and s.box[0] > anchors[-1] + 100), float("inf"))
    # Table width ends at the last header plus its column, not at the detail pane.
    right = min(right, anchors[-1] + max(120, anchors[-1]-anchors[-2]))
    body = [s for s in spans if s.box[1] > max(h.box[3] for h in header) + 5 and
            anchors[0]-5 <= s.box[0] < right]
    groups = []
    for span in sorted(body, key=lambda s: (s.box[1], s.box[0])):
        if not groups or abs(span.box[1] - groups[-1][0].box[1]) > 15:
            groups.append([])
        groups[-1].append(span)
    for group in groups:
        cells = [[] for _ in labels]
        for span in sorted(group, key=lambda s: s.box[0]):
            if re.fullmatch(r"\d+[\])]?$", span.text) and span.box[0] < anchors[0]+15:
                continue  # Row-number gutter is not part of a record ID.
            column = min(range(len(anchors)), key=lambda i: abs(span.box[0]-anchors[i]))
            cells[column].append(span)
        yield {**{label: tuple(cell) for label, cell in zip(labels, cells)}, "__spans__": tuple(group)}


def _cell(row, key, *, critical=True):
    spans = row[key]
    if critical and any(s.confidence < .90 for s in spans):
        return ""
    return " ".join(s.text for s in spans)


def normalize_screen(spans, source, context=()):
    """Interpret supported visible application schemas; low-confidence cells stay unknown."""
    texts = [s.text for s in spans]
    full = "\n".join(texts)
    facts, diagnostics = [], []
    def emit(system, resource, category, fields, region, row=None, **kwargs):
        if not resource:
            raise ValueError("resource identifier unreadable")
        ref = replace(source, locator="/image/region/" + region)
        region_spans = row["__spans__"] if row else [s for s in spans if s.box[0] > 220 and 80 < s.box[1] < 310]
        if region_spans:
            metadata = kwargs.pop("metadata", {})
            metadata["bounding_box"] = (min(s.box[0] for s in region_spans), min(s.box[1] for s in region_spans),
                                        max(s.box[2] for s in region_spans), max(s.box[3] for s in region_spans))
            metadata["unread_regions"] = [(s.text, s.box) for s in region_spans if s.confidence < .90]
            kwargs["metadata"] = metadata
        facts.append(_bind(_fact(ref, system, resource, category, fields, **kwargs), context))
    def card(label):
        labels = [s for s in spans if s.text == label and s.box[1] < 310 and s.box[0] > 220]
        for anchor in labels:
            values = [s for s in spans if 0 < s.box[1]-anchor.box[1] < 40 and abs(s.box[0]-anchor.box[0]) < 30 and s.confidence >= .90]
            if values:
                return min(values, key=lambda s: s.box[1]).text
        return ""
    try:
        if any(app in full for app in ("Customer Operations", "Pulse Analytics", "Helpline Console", "Audience Operations")):
            system, category = next((system, category) for app, system, category in (
                ("Customer Operations", "account_service", "USER_PROFILE"),
                ("Pulse Analytics", "analytics_platform", "ANALYTICS_PROFILE"),
                ("Helpline Console", "support_platform", "SUPPORT_MESSAGE"),
                ("Audience Operations", "marketing_platform", "MARKETING_PROFILE")) if app in full)
            headers = [s for s in spans if s.box[0] > 220 and 120 < s.box[1] < 165 and s.confidence >= .90]
            header = next((s for s in headers if re.search(ID, s.text)), None)
            if header is None:
                raise ValueError("subject header unreadable")
            ids = re.findall(ID, header.text)
            user = next((v for v in ids if v.startswith("user_")), None)
            resource = next((v for v in ids if v != user), None)
            fields = {}
            if user:
                fields["user_id"] = user
            email = re.search(EMAIL, header.text) or re.search(EMAIL, card("Email"))
            if email:
                fields["email"] = email[0]
            phone = card("Phone")
            if phone:
                fields["phone"] = phone
            emit(system, resource, category, fields, "subject-header-and-labelled-cards")
        elif "Search Console" in full:
            labels = ("_id", "Display name", "Email", "Customer reference", "Score", "Last indexed")
            for i, row in enumerate(_rows(spans, labels)):
                resource = _cell(row, "_id")
                if not re.fullmatch(ID, resource):
                    continue
                fields = {"email": _cell(row, "Email"), "display_name": _cell(row, "Display name")}
                reference = _cell(row, "Customer reference")
                if reference:
                    fields["customer_reference"] = reference
                emit("search_service", resource, "SEARCH_INDEX_ENTRY", fields, f"table/row/{i+1}", row=row)
        elif "BridgeOps" in full:
            labels = ("Connector", "Destination", "Selector", "Mode", "Events/hour", "Last contact")
            for i, row in enumerate(_rows(spans, labels)):
                resource = _cell(row, "Connector")
                if not re.fullmatch(ID, resource):
                    continue  # Display names alone do not supply resource IDs.
                fields = {"subject_selector": _cell(row, "Selector"), "destination": _cell(row, "Destination")}
                if _cell(row, "Mode") == "Continuous" and re.fullmatch(r"[\d,]+", _cell(row, "Events/hour")):
                    fields["enabled"] = True
                emit("integration_service", resource, "DATA_FLOW", fields, f"table/row/{i+1}", row=row)
        elif "Vault Recovery Manager" in full:
            labels = ("Archive ID", "Account", "Region", "Created", "Size", "Encryption", "Tier")
            for i, row in enumerate(_rows(spans, labels)):
                resource = _cell(row, "Archive ID")
                if re.fullmatch(ID, resource):
                    emit("backup_service", resource, "BACKUP_ARCHIVE", {"customer_email": _cell(row, "Account"),
                         "archive_present": True, "encryption": _cell(row, "Encryption")}, f"table/row/{i+1}", row=row)
        elif "Sentinel" in full:
            labels = ("Time (UTC)", "Event", "Category", "Subject", "Actor", "Integrity", "Reference")
            filter_text = next((text for text in texts if text.startswith("Subject filter:")), "")
            subject = re.search(ID, filter_text)
            for i, row in enumerate(_rows(spans, labels)):
                resource = _cell(row, "Reference")
                if re.fullmatch(ID, resource):
                    fields = {"event_name": _cell(row, "Event"), "integrity_seal": _cell(row, "Integrity")}
                    value = _cell(row, "Subject")
                    if value:
                        fields["subject_reference"] = value
                    emit("security_service", resource, "SECURITY_AUDIT_LOG", fields, f"table/row/{i+1}",
                         queries={"user_id": (subject[0],)} if subject else {}, row=row)
        elif "Customer Reference" in full and "Registry" in full:
            header = next((s for s in spans if 90 < s.box[1] < 125 and re.fullmatch(ID, s.text) and s.confidence >= .90), None)
            if header is None:
                raise ValueError("reference header unreadable")
            # UI presence proves a stable reference, not unrendered direct-field nulls.
            emit("identity_service", header.text, "ACCOUNT_TOMBSTONE", {"subject_reference": header.text}, "reference-header")
        elif "Customer Download Center" in full:
            labels = ("Export ID", "Filename", "Owner", "Status", "Created")
            for i, row in enumerate(_rows(spans, labels)):
                resource = _cell(row, "Export ID")
                if re.fullmatch(ID, resource):
                    emit("export_service", resource, "USER_EXPORT", {"owner_email": _cell(row, "Owner"),
                         "file_name": _cell(row, "Filename")}, f"table/row/{i+1}", row=row)
        elif "Nimbus Object Storage" in full:
            labels = ("Name", "Size", "Modified (UTC)", "Storage class", "Version")
            for i, row in enumerate(_rows(spans, labels)):
                name = _cell(row, "Name")
                if name:
                    emit("object_storage", name, "SUPPORT_ATTACHMENT", {"file_name": name}, f"table/row/{i+1}", row=row)
        else:
            raise ValueError("unreviewed screen layout")
        if not facts:
            raise ValueError("no grounded resource observations")
    except (ValueError, TypeError, KeyError):
        diagnostics.append(EvidenceDiagnostic("UNREAD_MEDIA_LAYOUT", source, "Required labels or identifiers unreadable"))
    return EvidenceBatch("screen-regions", tuple(facts), (), tuple(diagnostics), len(facts) + len(diagnostics), len(facts))


def normalize_pdf(pages, source, context=()):
    full = "\n".join(pages)
    facts, diagnostics = [], []
    def emit(system, resource, category, fields, page, **kwargs):
        facts.append(_bind(_fact(replace(source, locator=f"/pages/{page}"), system, resource, category,
                                 fields, **kwargs), context))
    if "COMPLIANCE JOURNAL - SIGNED RECORD EXTRACT" in full:
        resource = re.search(r"Event reference (\S+)", full)
        subject = re.search(r"subject reference (\S+)", full)
        until = re.search(r"retain until (\d{2} \w{3} \d{4} \d{2}\s+\d{2} UTC)", full)
        if resource and subject and until:
            emit("security_service", resource[1], "SECURITY_AUDIT_LOG", {"subject_reference": subject[1],
                 "retention_until": _date(until[1])}, 1)
    elif "DISPUTED ORDERS AND TAX RECORDS REGISTER" in full:
        for page, text in enumerate(pages, 1):
            for match in re.finditer(r"(ORD-[\w-]+) \| (\S+) \| [^\n]+?Retain until (\d{2} \w{3} \d{4})", text):
                emit("legal_service", match[1], "LEGAL_HOLD_RECORD", {"order_reference": match[1],
                     "subject_reference": match[2], "retained_until": _date(match[3])}, page)
    elif "CUSTOMER STATEMENT" in full:
        resource = re.search(r"Statement (\S+)", full)
        user = re.search(r"\buser_[\w]+\b", full)
        email = re.search(EMAIL, full)
        if resource and user and email:
            emit("export_service", resource[1], "USER_EXPORT", {"user_id": user[0], "email": email[0],
                 "document_text": full}, 1, metadata={"resource_aliases": (resource[1],)})
    elif "DELIVERY ADDRESS CORRECTION REQUEST" in full:
        resource = re.search(r"/ (\S+)", full)
        email = re.search(EMAIL, full)
        if resource and email:
            emit("support_platform", resource[1], "SUPPORT_ATTACHMENT", {"email": email[0], "document_text": full}, 1)
    if not facts:
        diagnostics.append(EvidenceDiagnostic("UNREAD_MEDIA_LAYOUT", source, "No grounded PDF document records"))
    return EvidenceBatch("pdf-text-layer", tuple(facts), (), tuple(diagnostics), len(pages), len(pages) if facts else 0)


def read_media_evidence(entry, ocr, context=()):
    raw = entry.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ValueError("media changed after manifest hashing")
    source = _source(entry)
    if entry.path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(raw) as document:
            pages = []
            for page in document:
                text = page.get_textpage().get_text_range()
                if text.strip():
                    pages.append(text)
                else:
                    spans = ocr.read(page.render(scale=2).to_pil())
                    pages.append("\n".join(s.text for s in spans if s.confidence >= .90))
        return normalize_pdf(pages, source, context)
    return normalize_screen(ocr.read(entry.path), source, context)
