"""test_json_reader.py – tests for phantom.json_reader."""

import hashlib, json, math, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from phantom.corpus import ManifestEntry
from phantom.json_reader import load_json_entry, walk_json


def _entry(raw: bytes, tmp: Path, name: str = "d.json") -> ManifestEntry:
    """Write raw bytes to tmp/name and return a matching ManifestEntry."""
    p = tmp / name
    p.write_bytes(raw)
    return ManifestEntry(
        file_id="f1", file_path=name, origin="test",
        captured_at="2026-01-01T00:00:00Z", path=p,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _je(val: object, tmp: Path, name: str = "d.json") -> ManifestEntry:
    return _entry(json.dumps(val).encode("utf-8"), tmp, name)


class TestLoadJsonEntry(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
    def tearDown(self):
        self._td.cleanup()

    # 1. Nested Unicode and optional UTF-8 BOM
    def test_unicode_and_bom(self):
        data = {"café": ["Ω", None, True, 42, 3.14]}
        raw_plain = json.dumps(data).encode("utf-8")
        raw_bom = b"\xef\xbb\xbf" + raw_plain
        with self.subTest("no BOM"):
            self.assertEqual(load_json_entry(_entry(raw_plain, self.d)), data)
        with self.subTest("with BOM"):
            self.assertEqual(load_json_entry(_entry(raw_bom, self.d)), data)

    # 2. Root array, scalar and null
    def test_root_variants(self):
        for val, desc in [
            ([1, 2, 3], "array"), ("hello", "string"),
            (None, "null"), (42, "int"), (False, "bool"),
        ]:
            with self.subTest(desc):
                self.assertEqual(load_json_entry(_je(val, self.d)), val)

    # 3. Duplicate object keys at root and nested levels
    def test_duplicate_keys_rejected(self):
        for raw_s, desc in [
            ('{"a": 1, "a": 2}', "root duplicate"),
            ('{"outer": {"k": 1, "k": 2}}', "nested duplicate"),
        ]:
            with self.subTest(desc):
                with self.assertRaises(ValueError):
                    load_json_entry(_entry(raw_s.encode(), self.d))

    # 4. NaN, Infinity, -Infinity and 1e999 rejection
    def test_nonfinite_rejected(self):
        for raw_s, desc in [
            ("[1e999]", "1e999"),
            ('{"v": NaN}', "NaN"),
            ('{"v": Infinity}', "Infinity"),
            ('{"v": -Infinity}', "-Infinity"),
        ]:
            with self.subTest(desc):
                with self.assertRaises(ValueError):
                    load_json_entry(_entry(raw_s.encode(), self.d))

    # 5. Malformed JSON and invalid UTF-8
    def test_malformed_and_bad_utf8(self):
        with self.subTest("malformed JSON"):
            with self.assertRaises(ValueError):
                load_json_entry(_entry(b"{bad json}", self.d))
        with self.subTest("invalid UTF-8"):
            with self.assertRaises(ValueError):
                load_json_entry(_entry(b"\xff\xfe" + b'{"x":1}', self.d))

    # 6. Hash mismatch and missing file
    def test_hash_mismatch_and_missing_file(self):
        with self.subTest("hash mismatch"):
            p = self.d / "bad.json"
            p.write_bytes(b"{}")
            entry = ManifestEntry(
                file_id="fx", file_path="bad.json", origin="test",
                captured_at="2026-01-01T00:00:00Z", path=p, sha256="0" * 64,
            )
            with self.assertRaises(ValueError):
                load_json_entry(entry)
        with self.subTest("missing file"):
            entry = ManifestEntry(
                file_id="fy", file_path="gone.json", origin="test",
                captured_at="2026-01-01T00:00:00Z",
                path=self.d / "gone.json", sha256="0" * 64,
            )
            with self.assertRaises((FileNotFoundError, OSError)):
                load_json_entry(entry)

    # 11. Source bytes remain unchanged after reading
    def test_source_bytes_unchanged(self):
        original = b'{"x": 1, "y": [true, null]}'
        entry = _entry(original, self.d)
        load_json_entry(entry)
        self.assertEqual((self.d / "d.json").read_bytes(), original)


class TestWalkJson(unittest.TestCase):

    # 7. Correct pointer preorder, RFC 6901 escaping and empty-string key
    def test_pointer_order_and_escaping(self):
        val = {"a/b": [None, {"~k": False}]}
        pointers = [p for p, _ in walk_json(val)]
        self.assertEqual(pointers, [
            "", "/a~1b", "/a~1b/0", "/a~1b/1", "/a~1b/1/~0k",
        ])
        with self.subTest("empty-string key"):
            pts = [p for p, _ in walk_json({"": 42})]
            self.assertEqual(pts, ["", "/"])

    # 8. Empty containers, None, False and zero remain distinct
    def test_falsy_values_all_yielded(self):
        val = [None, False, 0, [], {}]
        pairs = list(walk_json(val))
        nodes = [v for _, v in pairs]
        # root list + 5 elements + 0 children of primitives/empties = 6 pairs
        self.assertEqual(len(pairs), 6)
        self.assertIn(None, nodes)
        self.assertIn(False, nodes)
        self.assertIn(0, nodes)

    # 9. JSON-looking strings are not traversed as objects
    def test_json_string_not_parsed(self):
        val = {"key": '{"nested": 1}'}
        pointers = [p for p, _ in walk_json(val)]
        self.assertEqual(pointers, ["", "/key"])
        self.assertNotIn("/key/nested", pointers)

    # 10. Unsupported walker inputs raise ValueError
    def test_unsupported_inputs_raise(self):
        for bad, desc in [
            (float("nan"), "nan"),
            (float("inf"), "inf"),
            (b"bytes", "bytes object"),
            ({1: "v"}, "int key"),
        ]:
            with self.subTest(desc):
                with self.assertRaises(ValueError):
                    list(walk_json(bad))

    # 12. Traversal nodes reference the original objects (no copy)
    def test_nodes_are_original_references(self):
        inner = {"x": 1}
        val = [inner]
        found = [v for _, v in walk_json(val) if isinstance(v, dict)]
        self.assertEqual(len(found), 1)
        self.assertIs(found[0], inner)


if __name__ == "__main__":
    unittest.main()
