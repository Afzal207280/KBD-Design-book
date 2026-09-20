"""Acceptance §239: dependency invalidation, staleness, security utilities."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookfactory.state import DependencyState, ValidationLedger, DEPENDENCIES
from bookfactory.security import safe_filename, safe_join, redact, contain_ai_output


class TestDependencyInvalidation(unittest.TestCase):
    def test_upstream_change_invalidates_downstream(self):
        ds = DependencyState()
        before = dict(ds.versions)
        affected = ds.invalidate("PAGE_COUNT", "test")
        self.assertIn("PAGE_COUNT", affected)
        # spine, cover and every downstream node bumped
        for node in ("SPINE", "COVER", "INTERIOR_PDF", "COVER_PDF", "FINAL_AUDIT"):
            self.assertIn(node, affected)
            self.assertGreater(ds.versions[node], before[node])
        # upstream nodes untouched
        self.assertEqual(ds.versions["CONFIG"], before["CONFIG"])
        self.assertEqual(ds.versions["CONTENT"], before["CONTENT"])

    def test_every_node_exists_and_reaches_release(self):
        ds = DependencyState()
        affected = ds.invalidate("INTENT", "all")
        self.assertEqual(set(affected), set(DEPENDENCIES))


class TestValidationLedger(unittest.TestCase):
    def test_stale_pass_detected(self):
        ds = DependencyState()
        led = ValidationLedger()
        led.add({"artifact_id": "INTERIOR_PDF", "validator_id": "pdf-structural",
                 "artifact_version": ds.version_of("INTERIOR_PDF"), "status": "PASS"})
        self.assertEqual(led.stale_count(ds), 0)
        ds.invalidate("INTERIOR_PDF", "content changed")
        self.assertEqual(led.stale_count(ds), 1,
                         "PASS from an older artifact version must be stale")
        rec = led.latest_for("INTERIOR_PDF", "pdf-structural",
                             ds.version_of("INTERIOR_PDF"))
        self.assertFalse(rec["fresh"])


class TestSecurity(unittest.TestCase):
    def test_safe_filename(self):
        out = safe_filename("../../etc/passwd")
        self.assertNotIn("/", out)
        self.assertNotIn("\\", out)
        self.assertFalse(safe_filename("a" * 500).endswith(" "))
        self.assertLessEqual(len(safe_filename("b" * 500)), 80)

    def test_safe_join_blocks_traversal(self):
        with self.assertRaises(Exception):
            safe_join("/tmp/root", "..", "..", "etc/passwd")

    def test_redact_masks_secrets(self):
        out = redact("aws=AKIA1234567890ABCDEF token=ghp_abcdefabcdefabcdef12")
        self.assertNotIn("AKIA1234567890ABCDEF", out)
        self.assertNotIn("ghp_abcdefabcdefabcdef12", out)

    def test_contain_ai_output_enforces_schema(self):
        schema = {"title": str, "pages": int}
        good = contain_ai_output({"title": "X", "pages": 3}, schema)
        self.assertEqual(good["title"], "X")
        with self.assertRaises(Exception):
            contain_ai_output({"title": "X", "pages": 3, "evil": "; rm -rf /"}, schema)


if __name__ == "__main__":
    unittest.main()
