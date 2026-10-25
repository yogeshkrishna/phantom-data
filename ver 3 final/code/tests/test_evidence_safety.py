"""Synthetic fault-injection checks for untrusted SQL and derived OCR caches."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))

from ghost_audit.evidence import documents, sql_safety
from ghost_audit.evidence.parsers import parse_sql
from ghost_audit import pipeline

CAPTURED = datetime(2026, 8, 1, tzinfo=timezone.utc)
NORMAL_SQL = """
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE customer_profiles(resource_id TEXT PRIMARY KEY, email TEXT);
INSERT INTO customer_profiles VALUES('account_synthetic', 'synthetic@example.invalid');
CREATE TABLE embedded(resource_id TEXT, origin_system TEXT, collection_name TEXT,
                      record_json TEXT CHECK(json_valid(record_json)));
INSERT INTO embedded VALUES('search_synthetic', 'search_service', 'search_documents',
    '{"resource_id":"search_synthetic","email":"synthetic@example.invalid"}');
COMMIT;
"""


class SQLSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "snapshot.sql"

    def parse(self, text):
        self.path.write_text(text, encoding="utf-8")
        return parse_sql(self.path, "evidence_synthetic", CAPTURED)

    def test_legitimate_snapshot_keeps_rows_scopes_and_order(self):
        result = self.parse(NORMAL_SQL)
        self.assertEqual(result.notes, [])
        self.assertEqual([o.resource_id for o in result.observations], ["account_synthetic", "search_synthetic"])
        self.assertEqual([o.position for o in result.observations], [0, 0])
        self.assertTrue(all(o.present for o in result.observations))
        self.assertEqual({s.store for s in result.scopes}, {"customer_profiles", "embedded"})

    def test_unsafe_sql_is_rejected_without_partial_observations_or_external_file(self):
        outside = Path(self.temp.name) / "must_not_exist.sqlite"
        attacks = (
            f"ATTACH DATABASE '{outside.as_posix()}' AS external;",
            "DETACH DATABASE main;",
            "PRAGMA writable_schema=ON;",
            "PRAGMA journal_mode=WAL;",
            "SELECT load_extension('untrusted');",
            "CREATE VIRTUAL TABLE virtual_data USING fts5(contents);",
            "CREATE VIEW synthetic_view AS SELECT * FROM customer_profiles;",
            "CREATE TRIGGER synthetic_trigger AFTER INSERT ON customer_profiles BEGIN DELETE FROM customer_profiles; END;",
            "UPDATE customer_profiles SET email='changed@example.invalid';",
            "DELETE FROM customer_profiles;",
            "VACUUM INTO 'must_not_exist.sqlite';",
        )
        for attack in attacks:
            with self.subTest(attack=attack):
                result = self.parse(NORMAL_SQL + attack)
                self.assertEqual(result.observations, [])
                self.assertEqual(result.scopes, [])
                self.assertEqual(len(result.notes), 1)
                self.assertNotIn("example.invalid", result.notes[0])
                self.assertFalse(outside.exists())

    def test_recursive_source_is_bounded(self):
        query = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT x FROM n;"
        with patch.object(sql_safety, "MAX_SQL_STEPS", 2_000):
            result = self.parse(query)
        self.assertEqual(result.observations, [])
        self.assertIn("execution budget exceeded", result.notes[0])

    def test_wall_clock_and_source_size_budgets(self):
        with patch.object(sql_safety, "MAX_SQL_SECONDS", -1):
            result = self.parse("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT x FROM n;")
        self.assertIn("execution budget exceeded", result.notes[0])
        with patch.object(sql_safety, "MAX_SQL_BYTES", 4):
            result = self.parse(NORMAL_SQL)
        self.assertIn("byte budget", result.notes[0])

    def test_database_page_budget(self):
        with patch.object(sql_safety, "MAX_DATABASE_PAGES", 1):
            result = self.parse(NORMAL_SQL)
        self.assertEqual(result.observations, [])
        self.assertTrue(result.notes)

    def test_connections_close_after_success_rejection_and_inspection_failure(self):
        with sql_safety.snapshot_database("CREATE TABLE ordinary(x TEXT);") as con:
            self.assertEqual(con.execute("SELECT * FROM ordinary").fetchall(), [])
        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")
        con2 = None
        with self.assertRaises(sqlite3.DatabaseError):
            with sql_safety.snapshot_database("CREATE TABLE ordinary(x TEXT);") as con2:
                con2.execute("SELECT * FROM missing")
        with self.assertRaises(sqlite3.ProgrammingError):
            con2.execute("SELECT 1")

    def test_schema_identifiers_are_quoted_for_inspection(self):
        result = self.parse('CREATE TABLE "odd""name" (resource_id TEXT); INSERT INTO "odd""name" VALUES (\'account_99999\');')
        self.assertEqual(result.notes, [])
        self.assertEqual(result.observations[0].resource_id, "account_99999")


def _fake_ocr(_path):
    # Deliberately reverse spatial order; preserve V2's line reconstruction.
    return [
        ([[60, 10], [90, 10], [90, 20], [60, 20]], "right", 0.9),
        ([[0, 10], [40, 10], [40, 20], [0, 20]], "left", 0.9),
        ([[0, 50], [40, 50], [40, 60], [0, 60]], "below", 0.9),
    ], None


class OCRCacheSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "synthetic.png"
        self.path.write_bytes(b"synthetic-image-source")
        self.cache = self.root / "cache"
        self.identity = {"provider": "local", "engine": "synthetic-test-stub", "model_sha256": "a" * 64}
        self.cache_patch = patch.object(documents, "EVIDENCE_CACHE_DIR", self.cache)
        self.identity_patch = patch.object(documents, "ocr_engine_identity", side_effect=lambda: dict(self.identity))
        self.engine_patch = patch.object(documents, "_ocr_engine", return_value=_fake_ocr)
        self.cache_patch.start()
        self.identity_patch.start()
        self.engine_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.addCleanup(self.identity_patch.stop)
        self.addCleanup(self.engine_patch.stop)
        documents.reset_ocr_telemetry()

    def extract(self):
        return documents.image_text(self.path, "../untrusted-file-id")

    def cached_path(self):
        return next(self.cache.glob("*.json"))

    def test_valid_cache_preserves_reading_order_and_counts_actual_inference(self):
        text, method = self.extract()
        self.assertEqual((text, method), ("left  |  right\nbelow", "ocr"))
        self.assertEqual(self.extract(), (text, "ocr_cache"))
        stats = documents.get_ocr_telemetry()
        self.assertEqual((stats["image_requests"], stats["inference_calls"], stats["inference_successes"], stats["cache_hits"]),
                         (2, 1, 1, 1))
        envelope = json.loads(self.cached_path().read_text(encoding="utf-8"))
        self.assertEqual(envelope["source_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(envelope["engine_identity"], self.identity)
        self.assertTrue(self.cached_path().resolve().is_relative_to(self.cache.resolve()))

    def test_truncated_tampered_empty_stale_and_wrong_source_caches_recompute(self):
        expected = self.extract()[0]
        cache = self.cached_path()
        original = json.loads(cache.read_text(encoding="utf-8"))
        variants = [b"{", b"[]", b"null"]
        for field, value in (("text", "tampered"), ("schema_version", -1), ("source_sha256", "b" * 64),
                             ("engine_identity", {"engine": "old"})):
            altered = {**original, field: value}
            variants.append(json.dumps(altered).encode("utf-8"))
        variants.append(json.dumps({**original, "text": "", "text_sha256": hashlib.sha256(b"").hexdigest()}).encode("utf-8"))
        for payload in variants:
            with self.subTest(payload=payload[:60]):
                cache.write_bytes(payload)
                self.assertEqual(self.extract(), (expected, "ocr"))
        stats = documents.get_ocr_telemetry()
        self.assertEqual(stats["invalid_cache_entries"], len(variants))
        self.assertEqual(stats["inference_calls"], len(variants) + 1)

    def test_engine_upgrade_and_source_change_invalidate_key(self):
        expected = self.extract()[0]
        self.identity["model_sha256"] = "b" * 64
        self.assertEqual(self.extract(), (expected, "ocr"))
        self.path.write_bytes(b"new-synthetic-image-source")
        self.assertEqual(self.extract(), (expected, "ocr"))
        self.assertEqual(len(list(self.cache.glob("*.json"))), 3)

    def test_legacy_cache_is_not_trusted(self):
        self.cache.mkdir()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()[:16]
        (self.cache / f"synthetic_{digest}.txt").write_text("unverified legacy text", encoding="utf-8")
        self.assertEqual(self.extract()[1], "ocr")
        self.assertEqual(documents.get_ocr_telemetry()["cache_hits"], 0)

    def test_failed_atomic_cache_write_keeps_prior_bytes_and_valid_extraction(self):
        expected = self.extract()[0]
        cache = self.cached_path()
        cache.write_bytes(b"corrupt-prior-entry")
        with patch.object(documents, "atomic_write_bytes", side_effect=OSError("injected interrupted write")):
            self.assertEqual(self.extract(), (expected, "ocr"))
        self.assertEqual(cache.read_bytes(), b"corrupt-prior-entry")
        self.assertEqual(documents.get_ocr_telemetry()["cache_write_failures"], 1)

    def test_empty_results_are_never_cached(self):
        with patch.object(documents, "_ocr_engine", return_value=lambda _: ([], None)):
            self.assertEqual(self.extract(), ("", "ocr_empty"))
            self.assertEqual(self.extract(), ("", "ocr_empty"))
        self.assertFalse(self.cache.exists())
        self.assertEqual(documents.get_ocr_telemetry()["inference_calls"], 2)

    def test_missing_engine_cannot_reuse_cached_text(self):
        self.extract()
        with patch.object(documents, "ocr_engine_identity", side_effect=ImportError("missing dependency")):
            self.assertEqual(self.extract(), ("", "ocr_unavailable"))
        stats = documents.get_ocr_telemetry()
        self.assertEqual(stats["unavailable"], 1)
        self.assertEqual(stats["cache_hits"], 0)

    def test_source_mutation_during_inference_is_rejected(self):
        def mutating_ocr(path):
            self.path.write_bytes(b"changed-during-inference")
            return _fake_ocr(path)

        with patch.object(documents, "_ocr_engine", return_value=mutating_ocr):
            self.assertEqual(self.extract(), ("", "ocr_source_changed"))
        self.assertFalse(self.cache.exists())

    def test_run_reset_does_not_report_previous_model_calls(self):
        self.extract()
        documents.reset_ocr_telemetry()
        self.assertEqual(self.extract()[1], "ocr_cache")
        stats = documents.get_ocr_telemetry()
        self.assertEqual(stats["inference_calls"], 0)
        self.assertEqual(stats["cache_hits"], 1)
        self.assertEqual(stats["text_tokens"], None)


class CacheLocationGuardTests(unittest.TestCase):
    def test_corpus_cache_is_rejected_before_extraction_without_mutating_input(self):
        fixture = CODE / "evaluation" / "custom_cases" / "suite" / "data"
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "corpus"
            shutil.copytree(fixture, corpus)
            before = {p.relative_to(corpus): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in corpus.rglob("*") if p.is_file()}
            cache = corpus / "derived-cache"
            for destination in (cache, corpus):
                with self.subTest(destination=destination):
                    with patch.object(pipeline, "EVIDENCE_CACHE_DIR", destination), \
                            patch.object(documents, "EVIDENCE_CACHE_DIR", destination), \
                            patch.object(pipeline, "EvidenceIndex") as extraction:
                        with self.assertRaisesRegex(ValueError, "outside the read-only corpus"):
                            pipeline.run_pipeline(corpus)
                        extraction.assert_not_called()
            self.assertFalse(cache.exists())
            after = {p.relative_to(corpus): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in corpus.rglob("*") if p.is_file()}
            self.assertEqual(after, before)
            with patch.object(pipeline, "EVIDENCE_CACHE_DIR", Path(temporary) / "external-cache"), \
                    patch.object(documents, "EVIDENCE_CACHE_DIR", Path(temporary) / "external-cache"):
                self.assertEqual(len(pipeline.run_pipeline(corpus).results), 10)


if __name__ == "__main__":
    unittest.main()
