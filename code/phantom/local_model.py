"""Local neural OCR with source/model-addressed private extraction cache."""
import hashlib
import importlib.metadata
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

OCR_VERSION = "grounded-ocr.v1"


@dataclass(frozen=True, slots=True)
class TextSpan:
    text: str
    confidence: float
    box: tuple[float, float, float, float]
    page: int = 1


class LocalOCR:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir
        self.engine = None
        self.calls = self.cache_hits = 0
        self.seconds = 0.0

    def identity(self):
        try:
            import rapidocr
            directory = Path(rapidocr.__file__).parent / "models"
            models = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.onnx"))}
            if not models:
                raise RuntimeError("RapidOCR bundled models unavailable")
            return {"provider": "local CPU", "runtime": "onnxruntime", "runtime_version": importlib.metadata.version("onnxruntime"),
                    "package": "rapidocr", "package_version": importlib.metadata.version("rapidocr"),
                    "models": models, "adapter": OCR_VERSION, "threads": 2}
        except ImportError:
            raise RuntimeError("Install requirements.txt to enable local OCR") from None

    def read(self, image) -> tuple[TextSpan, ...]:
        import numpy as np
        from PIL import Image
        if isinstance(image, (str, Path)):
            with Image.open(image) as opened:
                pixels = np.array(opened.convert("RGB"))
        else:
            pixels = np.array(image.convert("RGB"))
        identity = self.identity()
        key = hashlib.sha256(pixels.tobytes() + str(pixels.shape).encode() +
                             json.dumps(identity, sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / (key + ".json") if self.cache_dir else None
        if path and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw["key"] == key:
                    spans = tuple(TextSpan(item["text"], float(item["confidence"]), tuple(item["box"])) for item in raw["spans"])
                    if all(isinstance(s.text, str) and 0 <= s.confidence <= 1 and len(s.box) == 4 for s in spans):
                        self.cache_hits += 1
                        return spans
            except (ValueError, TypeError, KeyError):
                pass  # Re-extract a corrupt/partial cache, never treat it as clean evidence.
        if self.engine is None:
            from rapidocr import RapidOCR
            self.engine = RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": 2,
                                          "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                                          "EngineConfig.onnxruntime.use_cuda": False,
                                          "Global.log_level": "warning"})
        started = time.perf_counter()
        result = self.engine(pixels)
        self.seconds += time.perf_counter() - started
        self.calls += 1
        spans = () if result.txts is None else tuple(TextSpan(text, float(score),
            (float(box[:, 0].min()), float(box[:, 1].min()), float(box[:, 0].max()), float(box[:, 1].max())))
            for text, score, box in zip(result.txts, result.scores, result.boxes))
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump({"key": key, "identity": identity, "spans": [asdict(s) for s in spans]}, handle)
                os.replace(temporary, path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        return spans

    def usage(self):
        return dict(self.identity(), calls=self.calls, cache_hits=self.cache_hits,
                    inference_seconds=self.seconds, input_tokens=None, output_tokens=None,
                    token_note="Neural OCR has no language-model token accounting", api_cost_usd=0)
