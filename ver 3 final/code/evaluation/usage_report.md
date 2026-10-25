Ghost Data Still Exists — final full-run usage report, 25 October 2026

This report concerns the selected full 518-request run that generated output.csv. Development coding-agent tokens, earlier experiments and later test/rehearsal work are separate and are not misrepresented as inference usage for this run.

Output SHA-256: `dd65f8801c9ab5dddc6a7c40a58d87138a503d77e166b95ad81e379d611c889c`. All 94 evidence files were processed; 518 rows and 3,127 steps were generated. Deterministic prediction used no paid text model. Local OCR is neural inference even though it has no text-token quantity.

| Provider / model | Calls | Input tokens | Output tokens | Total tokens | Tokens/request | Estimated API cost | Cost/request |
|---|---:|---:|---:|---:|---:|---:|---:|
| Local RapidOCR 1.2.3 via ONNX Runtime 1.31.0 | 11 OCR invocations | N/A | N/A | N/A | N/A | $0 external API | $0 external API |
| Paid text models (none) | 0 | 0 | 0 | 0 | 0 | $0 | $0 |
| Overall text-token/API totals | 0 paid calls; local OCR separate | 0 | 0 | 0 | 0 | $0 | $0 |

Local OCR: 11 image requests, 11 neural OCR invocations, 11 successful invocations, 0 failures, 0 cache hits, 0 invalid-cache entries, 0 cache-write failures. Average local OCR invocations/request: 0.021236. An invocation is one image request through the detector/classifier/recognizer pipeline, not three separately claimed API calls.

Cold source run measured 49.195 seconds before final CSV staging. Warm selected source rerun measured 2.345 seconds including serialization: zero OCR inferences and 11 cache hits, with identical output. These are individual local timings, not a performance guarantee. Local CPU/electricity costs were not measured and are not represented by the zero API-cost figure.

Exact installed OCR dependencies: rapidocr-onnxruntime==1.2.3, onnxruntime==1.31.0, numpy==2.2.5, opencv-python==5.0.0.93, pillow==13.0.0, pyclipper==1.4.0, shapely==2.2.0. PDF extraction uses pypdf 6.16.2; SQL uses Python's built-in SQLite. Python 3.12 Windows x64 was verified; Linux was not.

The OCR constructor uses installed defaults without overrides. Reading order uses box-center-line-v1. Cache keys bind the complete input SHA-256, installed engine/source/config/model hashes, Python major/minor and architecture. OCR model files in the installed package:

| Model resource | SHA-256 |
|---|---|
| models/ch_PP-OCRv3_det_infer.onnx | 3439588c030faea393a54515f51e983d8e155b19a2e8aba7891934c1cf0de526 |
| models/ch_PP-OCRv3_rec_infer.onnx | 897a3ededb38fee0dae2c1ccee38241f37df202c9509e3abca02e9217c5ee615 |
| models/ch_ppocr_mobile_v2.0_cls_infer.onnx | e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c |

The final telemetry JSON retains the complete installed package/config fingerprints outside the submitted code. The optional paid intent-classifier route was not exercised, is explicitly experimental, requires a separate output path and is not part of the release claim. Its provider/model availability and API behavior are unverified; unknown pricing remains unknown rather than inheriting another model's price. No credentials are included.
