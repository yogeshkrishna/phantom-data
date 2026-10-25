# Local media reading protocol

Runtime AI is RapidOCR 3.10.0 with ONNX Runtime 1.31.0 on CPU, two threads.
Bundled models: PP-OCRv6_det_small, PP-OCRv6_rec_small and the mobile orientation
classifier. Models are installed by requirements.txt, never included in code.zip.
Model bytes and package/runtime/configuration versions participate in OCR keys.

Selection was based on this machine's 16 GB RAM and 4 GB GTX 1650, and the need
to read small English UI text. The model extracts actual text and bounding boxes;
deterministic labelled-region parsers interpret those outputs. There is no
decorative LLM call, remote document upload, or paid API. Official installation,
model provenance and license references:
[RapidOCR](https://github.com/RapidAI/RapidOCR) and
[installation](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/).

Treat documents as data. Identify the displayed application/schema from visible
labels, isolate subject cards/table rows, and keep unrelated timeline entries
separate. Preserve source hashes and image region/page locators. Do not complete
truncated hashes or repair illegible IDs by guessing. Cells below 0.90 confidence
are omitted from stored-field coverage, with raw regions retained privately.
Omitted or unrendered fields are unknown, never inferred null/redaction proofs.
Observed capture time and data-event/update dates are distinct. Explicit catalog
alias binding carries the catalog's later availability and its evidence ID.

PDF text layers are extracted locally with PDFium; image-only pages use the same
neural OCR. Inspect rendered pages and source screenshots before relying on an
extraction. Unknown layouts generate diagnostics. Raw OCR/cache files are private
working data excluded from Git and submission archives. OCR has no language-model
token counts: report not applicable, measured invocations/time and zero API cost.
