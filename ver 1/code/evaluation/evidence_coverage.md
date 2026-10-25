# Evidence integration checkpoint (stage 08)

This is coverage and runtime evidence, not a final accuracy report.

- All 94 manifest files attempted; 79 interpreted: 28 JSON, 24 SQL, 26 logs, one text.
- All 2,255 nonblank log/text events have reviewed semantic or contextual roles;
  1,111 state observations emitted, zero format/semantic diagnostics on these files.
- All 4,011 activity rows preserved: 3,882 claims, 117 explicit checker readbacks,
  12 catalog identity statements. All catalog statements parse, including external
  document references and the explicitly exclusive attachment ownership statement.
- Remaining unsupported evidence: 11 PNG images and four PDFs. Visible unsupported
  evidence prevents COMPLETE. Source observation and availability cutoffs both apply.
- All 94 evidence file hashes remained unchanged after integration and the run.

Interim execution attempted all 518 requests. It returned 376 INCOMPLETE,
10 NOT_APPLICABLE and two supported COMPLETE results; 130 requests blocked because
completion was unproven and no supported finding could honestly populate the
required INCOMPLETE schema. No final 518-row submission was manufactured.
These counts measure execution coverage, not correctness against hidden answers.
Outstanding gaps include media interpretation, retention-condition proof and
readbacks for otherwise unresolved resources/risks.

Measured locally before final packaging: preparation 1.309 seconds; prepared
518-request execution, including independent verification, 1.124 seconds. These
exclude future local-model/media preparation and do not prove the final full
pipeline runtime. No inference model or paid API was called.

The log parser handles key/value, JSON, syslog, application bracket, pipe and
escaped serverless-message envelopes, plus multiline JSON. File-local trace links
require matching service, instance, pod, region, trace ID, readback family and an
agreeing explicit resource ID when supplied. Unrelated events sharing timestamps,
trace fragments or nearby lines do not create identity links. Query selectors stay
separate from stored resource PII. The diagnostic and handover formats supply
explicit semantic readbacks without inventing stored payload from lookup echoes.

Activity SUCCESS on deletion/redaction/sync/restore remains a claim. Explicit
checker statements can support current risks; explicit catalog statements can
support identity links. A claimed write at/after an old readback invalidates a
positive completion proof until a newer readback resolves it. Unknown formats
remain diagnostics. Retained archive presence and restore blocking are separate
checks. Reassigned-resource ownership blocks destructive planning.

CLI subprocess checks confirmed exit 0 for a supported selected main request,
exit 2 for an unknown case, and preservation of a previously written output on
failure. Regression tests cover semantic counterexamples and verifier mutations;
the current complete test command/count is maintained in WORK_STATE.md.
