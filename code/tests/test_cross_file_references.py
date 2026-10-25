"""Exact dangling references authorize future checks, not invented state proof."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.contracts import EntityKey, EvidenceRef
from phantom.pipeline import audit_request
from phantom.references import bind_resource_references
from phantom.state import StateIndex
from phantom.planner import AuditBlocked
from test_pipeline import prepared, request
from test_state import A, B, C, fact, subject


def sources(target_time=A, target_source=None):
    search = fact({"email": "a@example.test", "source_resource_id": "opaque-target"}, B,
                  entity=EntityKey("search_service", "search_documents_primary", "opaque-index"),
                  category="SEARCH_INDEX_ENTRY")
    target = fact({}, target_time, kind="claim", category="UNKNOWN",
                  entity=EntityKey("account_service", "account_service", "opaque-target"),
                  metadata={"event_type": "DELETE_RECORD", "reported_result": "SUCCESS"},
                  **({"source": target_source} if target_source else {}))
    return search, target


class CrossFileReferenceTests(unittest.TestCase):
    def test_cross_file_operation_binds_relevance_without_proving_absence(self):
        linked = bind_resource_references(sources())
        context = prepared(linked)
        result = audit_request(context, request())
        self.assertEqual(result.deletion_status, "INCOMPLETE")
        self.assertEqual({(s.system_id, s.resource_id) for s in result.steps if s.phase == "VERIFICATION"},
                         {("search_service", "opaque-index"), ("account_service", "opaque-target")})
        target = next(s for s in context.states.resolve(subject(), C).resources if s.entity.system_id == "account_service")
        self.assertEqual(target.state, "UNKNOWN")
        self.assertFalse(target.latest)
        self.assertFalse(any(s.system_id == "account_service" for s in result.steps if s.phase == "FINDING"))

    def test_ambiguous_target_service_and_numeric_similarity_do_not_join(self):
        search, target = sources()
        other = replace(target, entity=EntityKey("other_service", "other_service", "opaque-target"))
        self.assertEqual(bind_resource_references((search, target, other)), (search, target, other))
        renamed = replace(target, entity=replace(target.entity, resource_id="opaque-target-2"))
        self.assertEqual(bind_resource_references((search, renamed)), (search, renamed))

    def test_future_witness_does_not_backdate_a_reference(self):
        future = EvidenceRef("future", "future.csv", "a"*64, "/row", "2030-01-01T00:00:00Z")
        linked = bind_resource_references(sources(target_source=future))
        resources = prepared(linked).states.resolve(subject(), C).resources
        self.assertEqual({r.entity.system_id for r in resources}, {"search_service"})
        # Independently guard preexisting explicit edges against invisible targets.
        search, target = sources(target_source=future)
        search = replace(search, references=(target.entity,))
        self.assertEqual({r.entity.system_id for r in StateIndex((search, target)).resolve(subject(), C).resources},
                         {"search_service"})

    def test_unknown_target_blocks_complete_instead_of_accepting_worker_success(self):
        search, target = sources()
        gone = replace(search, observed_at=C, state="ABSENT", stored_fields={}, field_coverage=())
        context = prepared(bind_resource_references((search, target, gone)))
        with self.assertRaises(AuditBlocked):
            audit_request(context, request())

    def test_unknown_category_cannot_borrow_a_future_type(self):
        search, target = sources()
        target = replace(target, entity=EntityKey("support_platform", "support_platform", "opaque-target"))
        future = replace(target, category="SUPPORT_ATTACHMENT", observed_at="2030-01-01T00:00:00Z")
        result = bind_resource_references((search, target, future))
        self.assertEqual({r.entity.system_id for r in prepared(result).states.resolve(subject(), C).resources},
                         {"search_service"})


if __name__ == "__main__":
    unittest.main()
