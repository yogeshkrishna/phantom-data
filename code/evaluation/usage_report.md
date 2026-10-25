# Measured runtime usage

Run: 2026-10-25T02:22:15.337426+00:00. Gate: BLOCKED.

Attempted 518 requests; 457 supported; 61 blocked. output.csv produced: False.

This is a measured full-input attempt. A blocked attempt is not a compliant final-output run; regenerate this report after all release gates pass.

| Provider/model | Calls in this attempt | Input/output tokens | API cost USD |
|---|---|---|---|
| Local CPU RapidOCR 3.10.0 / ONNX Runtime 1.31.0 | 0 | N/A: OCR has no LLM tokens | 0 |

OCR extraction-cache hits: 0. Summed engine inference: 0.000s. Compiled cache hit: True.

Media wall time: 0.000s; OCR workers used: 0. Parallel engine time may exceed elapsed wall time.

No language model runs in the audit pipeline. Overall LLM calls/tokens: 0. Average LLM tokens per attempted request: 0. Total/per-request paid API cost: USD 0. Local compute, electricity and development/coding-model usage are outside this runtime API figure.

## Model identity

- PP-OCRv6_det_small.onnx: SHA-256 `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f`
- PP-OCRv6_rec_small.onnx: SHA-256 `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884`
- ch_ppocr_mobile_v2.0_cls_mobile.onnx: SHA-256 `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c`

Calls count whole OCR pipeline passes; internal detector/recognizer/classifier invocations are not individually instrumented. No per-component call totals are invented.

Cached extraction/preparation may have used model inference earlier. That work is not counted as new inference in this attempt; the machine-readable run report preserves preparation_provenance where available. Cold and warm timings are reported separately when a clean-cache package rehearsal is performed.
