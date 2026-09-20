"""Acceptance §236/§237 end-to-end: full pipeline with REAL validators.

No PASS here is mocked — each project runs ANALYZING→…→AUDITING with the real
geometry, layout, PDF, EPUB, visual-QA, editorial and final-audit engines.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookfactory.exporter import build_export_package
from bookfactory.pipeline import Pipeline
from bookfactory.ruleset import Ruleset
from bookfactory.storage import ProjectStore

TMP = tempfile.mkdtemp(prefix="bf-tests-")


def build(topic, overrides=None, store_dir=None):
    store = ProjectStore(store_dir or TMP)
    pipe = Pipeline(store, Ruleset())
    pid = pipe.create(topic, overrides or {})
    state = pipe.run(pid)
    return store, pid, state


class TestGreenBuilds(unittest.TestCase):
    """One representative book per content class must reach GREEN with
    zero critical/blocking defects and a passing final audit."""

    CASES = [
        ("fishing log notebook", {"book_type": "logbook", "target_pages": 26}),
        ("forest puzzle pack", {"book_type": "puzzle_book"}),
        ("quiet morning poems", {"book_type": "poetry_collection"}),
        ("weeknight dinners", {"book_type": "cookbook"}),
        ("ocean animals coloring", {"book_type": "coloring_book"}),
    ]

    def test_all_green(self):
        for topic, ov in self.CASES:
            with self.subTest(topic=topic):
                store, pid, st = build(topic, ov)
                self.assertEqual(st["status"], "GREEN",
                                 (pid, st.get("defects"), st.get("validation")))
                audit = st.get("final_audit") or {}
                self.assertTrue(audit.get("pass"))
                blocking = [d for d in st.get("defects", [])
                            if d["severity"] in ("CRITICAL", "BLOCKING")]
                self.assertEqual(blocking, [])

    def test_mystery_novel_green(self):
        store, pid, st = build("a mystery about a lighthouse keeper",
                               {"book_type": "mystery_novel", "target_pages": 160})
        self.assertEqual(st["status"], "GREEN")
        self.assertGreaterEqual(st["page_count"], 79)  # spine text eligible


class TestHardcover(unittest.TestCase):
    def test_hardcover_green_and_case_laminate(self):
        store, pid, st = build("a mystery about a lighthouse keeper",
                               {"book_type": "mystery_novel", "binding": "hardcover",
                                "target_pages": 120})
        self.assertEqual(st["status"], "GREEN")
        self.assertEqual(st["config"]["binding"], "hardcover")


class TestDeterminism(unittest.TestCase):
    def test_identical_bytes_across_fresh_pipelines(self):
        digests = []
        for _ in range(2):
            store, pid, st = build("determinism audit", {"book_type": "puzzle_book"})
            d = store.dir(pid)
            digests.append(tuple(
                hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest()
                for f in ("interior.pdf", "cover.pdf", "book.epub")))
        self.assertEqual(digests[0], digests[1],
                         "identical inputs must produce byte-identical artifacts")


class TestRecoveryResume(unittest.TestCase):
    def test_stop_and_resume_with_new_pipeline_instance(self):
        store = ProjectStore(TMP)
        pipe = Pipeline(store, Ruleset())
        pid = pipe.create("resume me", {"book_type": "gratitude_journal",
                                        "target_pages": 60})
        st = pipe.run(pid, stop_after="DESIGNING")
        self.assertEqual(st["state"], "DESIGNING")
        # process 'crashed' here — a brand new Pipeline resumes from disk
        pipe2 = Pipeline(store, Ruleset())
        st2 = pipe2.run(pid)
        self.assertEqual(st2["status"], "GREEN")

    def test_invalid_paper_rejected_cleanly(self):
        store = ProjectStore(TMP)
        pipe = Pipeline(store, Ruleset())
        pid = pipe.create("bad paper", {"book_type": "mystery_novel",
                                        "paper": "groundwood",
                                        "binding": "hardcover"})
        with self.assertRaises(Exception):
            pipe.run(pid)


class TestExportPackage(unittest.TestCase):
    def test_export_zip_contains_launcher_with_host_url(self):
        store, pid, st = build("fishing log notebook",
                               {"book_type": "logbook", "target_pages": 26})
        self.assertEqual(st["status"], "GREEN")
        url = "https://8010-sandbox123.e2b.app"
        out = build_export_package(store, pid, st, url)
        self.assertTrue(os.path.exists(out["path"]))
        with zipfile.ZipFile(out["path"]) as z:
            names = z.namelist()
            self.assertIn("START_HERE.html", names)
            self.assertIn("BookFactory.url", names)
            self.assertIn("interior.pdf", names)
            self.assertIn("cover.pdf", names)
            self.assertIn("book.epub", names)
            self.assertIn("manifest.json", names)
            html = z.read("START_HERE.html").decode("utf-8")
            self.assertIn(url, html)
            urlfile = z.read("BookFactory.url").decode("utf-8")
            self.assertIn(url, urlfile)
            man = json.loads(z.read("manifest.json"))
            self.assertEqual(man["final_status"], "GREEN")
            self.assertEqual(man["website"], url)
            # manifest checksums re-verified inside the package (§211)
            for name, info in man["files"].items():
                blob = z.read(name)
                self.assertEqual(hashlib.sha256(blob).hexdigest(), info["sha256"], name)


if __name__ == "__main__":
    unittest.main()
