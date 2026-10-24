"""test_corpus.py – unit tests for phantom.corpus."""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phantom.corpus import (
    MANIFEST_COLUMNS,
    ManifestEntry,
    read_csv_table,
    read_manifest,
)


def _write(directory: Path, name: str, content: bytes) -> Path:
    """Write bytes to directory/name (creating parents) and return the path."""
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dataset(tmp: Path, rows: list[str], files: dict[str, bytes]) -> Path:
    """Build a self-contained dataset_dir in tmp."""
    header = "file_id,file_path,origin,captured_at\n"
    _write(tmp, "evidence_files.csv", (header + "".join(rows)).encode("utf-8"))
    for rel, data in files.items():
        _write(tmp, rel, data)
    return tmp


def _fresh(rows: list[str], files: dict[str, bytes] | None = None) -> Path:
    """Create a throwaway temp dir with a dataset; caller must clean up."""
    d = Path(tempfile.mkdtemp())
    _dataset(d, rows, files or {})
    return d


def _directory_link(test: unittest.TestCase, target: Path, link: Path) -> None:
    """Use a directory junction on Windows when symlink privilege is absent."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            test.skipTest("Directory symlinks unavailable")
        import _winapi

        try:
            _winapi.CreateJunction(str(target), str(link))
        except OSError:
            test.skipTest("Directory symlinks and junctions unavailable")


# ---------------------------------------------------------------------------
# read_csv_table tests
# ---------------------------------------------------------------------------

class TestReadCsvTable(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _csv(self, name: str, content: bytes) -> Path:
        return _write(self.d, name, content)

    def test_bom_and_unicode(self) -> None:
        p = self._csv("u.csv", "\ufeffid,v\ncafé,résumé\n".encode("utf-8"))
        rows = read_csv_table(p, ("id", "v"), "id")
        self.assertEqual(rows[0], {"id": "café", "v": "résumé"})

    def test_quoted_comma_and_embedded_newline(self) -> None:
        p = self._csv("q.csv", 'id,v\n"a,b","line1\nline2"\n'.encode("utf-8"))
        rows = read_csv_table(p, ("id", "v"), "id")
        self.assertEqual(rows[0]["id"], "a,b")
        self.assertEqual(rows[0]["v"], "line1\nline2")

    def test_double_quote_escape(self) -> None:
        p = self._csv("dq.csv", 'id,v\nfoo,"say ""hi"""\n'.encode("utf-8"))
        self.assertEqual(read_csv_table(p, ("id", "v"), "id")[0]["v"], 'say "hi"')

    def test_malformed_csv_raises_value_error(self) -> None:
        for content in (b'"id,v\n', b'id,v\nx,"unterminated\n',
                        b'id,v\nx,"quoted"trailing\n'):
            with self.subTest(content=content):
                p = self._csv("malformed.csv", content)
                with self.assertRaises(ValueError):
                    read_csv_table(p, ("id", "v"), "id")

    def test_newline_styles_and_embedded_crlf_preserved(self) -> None:
        for ending in (b"\n", b"\r\n", b"\r"):
            with self.subTest(ending=ending):
                p = self._csv("newlines.csv", b"id,v" + ending +
                              b'x,"first\r\nsecond"' + ending)
                self.assertEqual(read_csv_table(p, ("id", "v"), "id"),
                                 [{"id": "x", "v": "first\r\nsecond"}])

    def test_blank_records_skipped_but_empty_id_rejected(self) -> None:
        p = self._csv("blank.csv", b"id,v\n\nx,ok\n\n")
        self.assertEqual(read_csv_table(p, ("id", "v"), "id"),
                         [{"id": "x", "v": "ok"}])
        p.write_bytes(b"id,v\n,\n")
        with self.assertRaises(ValueError):
            read_csv_table(p, ("id", "v"), "id")

    def test_duplicate_id_error_does_not_expose_value(self) -> None:
        p = self._csv("duplicate.csv", b"id,v\nprivate@example.test,1\nprivate@example.test,2\n")
        with self.assertRaises(ValueError) as caught:
            read_csv_table(p, ("id", "v"), "id")
        self.assertNotIn("private@example.test", str(caught.exception))

    def test_row_order_and_original_text_preserved(self) -> None:
        p = self._csv("ord.csv", "id,v\nC, 3 \nA, 1 \n".encode("utf-8"))
        rows = read_csv_table(p, ("id", "v"), "id")
        self.assertEqual([r["id"] for r in rows], ["C", "A"])
        self.assertEqual(rows[0]["v"], " 3 ")

    def test_header_only_returns_empty(self) -> None:
        p = self._csv("h.csv", b"id,v\n")
        self.assertEqual(read_csv_table(p, ("id", "v"), "id"), [])

    def test_bad_headers_raise(self) -> None:
        for content, desc in [
            ("wrong,cols\nv1,v2\n", "wrong columns"),
            ("v,id\ni1,v1\n", "reordered"),
            ("id,v,extra\ni1,v1,x\n", "extra column"),
        ]:
            with self.subTest(desc):
                p = self._csv(f"h_{desc}.csv", content.encode())
                with self.assertRaises(ValueError):
                    read_csv_table(p, ("id", "v"), "id")

    def test_cell_count_errors_raise(self) -> None:
        for content, desc in [
            (b"id,v\ni1\n", "missing cell"),
            (b"id,v\ni1,v1,extra\n", "extra cell"),
        ]:
            with self.subTest(desc):
                p = self._csv(f"c_{desc}.csv", content)
                with self.assertRaises(ValueError):
                    read_csv_table(p, ("id", "v"), "id")

    def test_blank_and_duplicate_ids_raise(self) -> None:
        for content, desc in [
            (b"id,v\n   ,v1\n", "blank id"),
            (b"id,v\ndup,v1\ndup,v2\n", "duplicate id"),
        ]:
            with self.subTest(desc):
                p = self._csv(f"id_{desc}.csv", content)
                with self.assertRaises(ValueError):
                    read_csv_table(p, ("id", "v"), "id")

    def test_invalid_config_raises(self) -> None:
        p = self._csv("cfg.csv", b"id,v\ni1,v1\n")
        with self.subTest("empty columns"):
            with self.assertRaises(ValueError):
                read_csv_table(p, (), "id")
        with self.subTest("id_column absent"):
            with self.assertRaises(ValueError):
                read_csv_table(p, ("id", "v"), "missing")
        with self.subTest("duplicate column names"):
            with self.assertRaises(ValueError):
                read_csv_table(p, ("id", "id"), "id")

    def test_invalid_utf8_raises(self) -> None:
        p = self._csv("bad.csv", b"id,v\n\xff\xfe,val\n")
        with self.assertRaises((ValueError, UnicodeDecodeError)):
            read_csv_table(p, ("id", "v"), "id")

    def test_input_file_unchanged(self) -> None:
        original = b"id,v\nfoo,bar\n"
        p = self._csv("ro.csv", original)
        read_csv_table(p, ("id", "v"), "id")
        self.assertEqual(p.read_bytes(), original)


# ---------------------------------------------------------------------------
# read_manifest tests
# ---------------------------------------------------------------------------

class TestReadManifest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build(self, rows: list[str], files: dict[str, bytes]) -> None:
        _dataset(self.d, rows, files)

    def test_header_only_returns_empty_tuple(self) -> None:
        self._build([], {})
        self.assertEqual(read_manifest(self.d), ())

    def test_resolved_path_sha256_and_metadata(self) -> None:
        data = b"hello evidence"
        self._build(
            ["e1,evidence/f.bin,my-src,2026-01-01T00:00:00Z\n"],
            {"evidence/f.bin": data},
        )
        (e,) = read_manifest(self.d)
        self.assertEqual(e.file_id, "e1")
        self.assertEqual(e.file_path, "evidence/f.bin")
        self.assertEqual(e.origin, "my-src")
        self.assertEqual(e.captured_at, "2026-01-01T00:00:00Z")
        self.assertTrue(e.path.is_absolute())
        self.assertEqual(e.sha256, _sha256(data))

    def test_original_metadata_verbatim(self) -> None:
        ts = "2025-06-15T08:30:00+05:30"
        self._build([f"id1,evidence/x.bin,s,{ts}\n"], {"evidence/x.bin": b"x"})
        self.assertEqual(read_manifest(self.d)[0].captured_at, ts)

    def test_row_order_preserved(self) -> None:
        self._build(
            ["e3,evidence/c.bin,s,2026-01-01T00:00:00Z\n",
             "e1,evidence/a.bin,s,2026-01-01T00:00:00Z\n",
             "e2,evidence/b.bin,s,2026-01-01T00:00:00Z\n"],
            {"evidence/a.bin": b"a", "evidence/b.bin": b"b", "evidence/c.bin": b"c"},
        )
        self.assertEqual([e.file_id for e in read_manifest(self.d)], ["e3", "e1", "e2"])

    def test_two_ids_same_path_allowed(self) -> None:
        self._build(
            ["eA,evidence/shared.bin,s,2026-01-01T00:00:00Z\n",
             "eB,evidence/shared.bin,s,2026-01-01T00:00:00Z\n"],
            {"evidence/shared.bin": b"shared"},
        )
        entries = read_manifest(self.d)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].sha256, entries[1].sha256)

    def test_empty_evidence_file_hashed(self) -> None:
        self._build(["e0,evidence/e.bin,s,2026-01-01T00:00:00Z\n"], {"evidence/e.bin": b""})
        self.assertEqual(read_manifest(self.d)[0].sha256, _sha256(b""))

    def test_bad_captured_at_raises(self) -> None:
        for ts, desc in [("not-a-date", "junk"), ("2026-01-01T00:00:00", "naive")]:
            with self.subTest(desc):
                d = _fresh([f"e1,evidence/f.bin,s,{ts}\n"], {"evidence/f.bin": b"x"})
                try:
                    with self.assertRaises(ValueError):
                        read_manifest(d)
                finally:
                    shutil.rmtree(d, ignore_errors=True)

    def test_unsafe_paths_raise(self) -> None:
        bad_paths = [
            ("/etc/passwd", "posix absolute"),
            ("C:/Windows/x.exe", "windows drive"),
            ("C:relative.bin", "windows drive relative"),
            ("C:\\Windows\\x.exe", "windows backslash drive"),
            ("\\rooted.bin", "windows rooted"),
            ("\\\\server\\share\\f", "windows unc"),
            ("//server/share/f", "unc path"),
            ("evidence/../../../etc/passwd", "dotdot"),
            ("evidence/fi\x00le.bin", "nul char"),
        ]
        for rel, desc in bad_paths:
            with self.subTest(desc):
                d = _fresh([f"e1,{rel},s,2026-01-01T00:00:00Z\n"])
                try:
                    with self.assertRaises(ValueError):
                        read_manifest(d)
                finally:
                    shutil.rmtree(d, ignore_errors=True)

    def test_missing_evidence_raises(self) -> None:
        self._build(["e1,evidence/gone.bin,s,2026-01-01T00:00:00Z\n"], {})
        with self.assertRaises(FileNotFoundError):
            read_manifest(self.d)

    def test_directory_target_raises(self) -> None:
        (self.d / "evidence" / "subdir").mkdir(parents=True, exist_ok=True)
        self._build(["e1,evidence/subdir,s,2026-01-01T00:00:00Z\n"], {})
        with self.assertRaises(ValueError):
            read_manifest(self.d)

    def test_missing_manifest_raises(self) -> None:
        empty = self.d / "no_csv"
        empty.mkdir()
        with self.assertRaises(FileNotFoundError):
            read_manifest(empty)

    def test_symlink_escape_raises(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external).resolve()
            self.assertFalse(outside.is_relative_to(self.d.resolve()))
            secret = outside / "secret.bin"
            secret.write_bytes(b"secret")
            link = self.d / "evidence"
            _directory_link(self, outside, link)
            try:
                self._build(["e1,evidence/secret.bin,s,2026-01-01T00:00:00Z\n"], {})
                with self.assertRaises(ValueError):
                    read_manifest(self.d)
                self.assertEqual(secret.read_bytes(), b"secret")
            finally:
                if link.is_symlink():
                    link.unlink()
                else:
                    link.rmdir()

    def test_internal_directory_link_allowed(self) -> None:
        target = self.d / "original"
        target.mkdir()
        (target / "item.bin").write_bytes(b"retained")
        link = self.d / "evidence"
        _directory_link(self, target, link)
        try:
            self._build(["e1,evidence/item.bin,s,2026-01-01T00:00:00Z\n"], {})
            self.assertEqual(read_manifest(self.d)[0].path, (target / "item.bin").resolve())
        finally:
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()

    def test_backslash_relative_path_allowed(self) -> None:
        self._build(["e1,evidence\\item.bin,s,2026-01-01T00:00:00Z\n"],
                    {"evidence/item.bin": b"same bytes"})
        self.assertEqual(read_manifest(self.d)[0].path,
                         (self.d / "evidence" / "item.bin").resolve())


if __name__ == "__main__":
    unittest.main()
