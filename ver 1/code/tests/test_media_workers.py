"""Parallel media extraction isolates engines, preserves order and meters usage."""
import sys
import hashlib
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.compiler import _media_batches
from phantom.media_evidence import read_media_evidence


class FakeOCR:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.calls = self.cache_hits = 0
        self.seconds = 0.0
        self.owner = None

    def usage(self):
        return dict(provider="fixture", calls=self.calls, cache_hits=self.cache_hits,
                    inference_seconds=self.seconds)


class MediaWorkerTests(unittest.TestCase):
    def test_pdfium_native_document_access_is_serialized(self):
        active, maximum = 0, 0

        class Document:
            def __init__(self, raw):
                pass

            def __enter__(self):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                time.sleep(.01)
                return self

            def __exit__(self, *args):
                nonlocal active
                active -= 1

            def __iter__(self):
                return iter(())

        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.pdf"
            path.write_bytes(b"synthetic PDF backend fixture")
            entry = SimpleNamespace(path=path, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                    file_id="fixture", file_path="fixture.pdf", captured_at="2026-01-01T00:00:00Z")
            with patch.dict("sys.modules", {"pypdfium2": SimpleNamespace(PdfDocument=Document)}):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(lambda _: read_media_evidence(entry, FakeOCR()), range(4)))
        self.assertEqual(maximum, 1)
        self.assertEqual(active, 0)

    def test_order_engine_isolation_usage_and_parse_failures(self):
        context = ("explicit catalog",)

        def reader(entry, engine, catalog):
            if engine.owner is None:
                engine.owner = threading.get_ident()
            self.assertEqual(engine.owner, threading.get_ident())
            self.assertIs(catalog, context)
            engine.calls += 1
            engine.seconds += .01
            time.sleep(.01)
            if entry == "bad":
                raise ValueError("unsupported media")
            return entry

        with patch("phantom.compiler.LocalOCR", FakeOCR), patch("phantom.compiler.read_media_evidence", reader):
            batches, usage = _media_batches(["first", "bad", "third", "fourth"], FakeOCR(), context)
        self.assertEqual(batches, ["first", None, "third", "fourth"])
        self.assertEqual(usage["calls"], 4)
        self.assertAlmostEqual(usage["inference_seconds"], .04)
        self.assertEqual(usage["workers_used"], 2)
        self.assertGreater(usage["media_wall_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
