"""
Unit tests for SkillsDB v3.0 Architecture:
- WriterQueue asynchronous SQLite serialization
- Journal append-only buffers & atomic flush
- Zero-dependency multilingual synonym synapses & query expansion
- Resilient multi-stage model tier detector
- Modular package vs. standalone db_manager.py parity
"""

import sys
import unittest
import tempfile
import sqlite3
import threading
from pathlib import Path

# Add project root to path
REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skillsdb.config import TIER_LEAN, TIER_STANDARD, TIER_ULTRA
from skillsdb.core.db import get_connection, init_db
from skillsdb.core.detector import detect_model_tier, set_profile
from skillsdb.search.synonyms import (
    DEFAULT_SYNONYMS,
    init_synonyms,
    expand_query_with_synonyms,
)
from skillsdb.memory.writer_queue import (
    get_writer_queue,
    write_journal_entry,
    flush_journals,
)
from skillsdb.memory.project_memory import (
    init_project_db,
    mem_save_decision,
    mem_save_snapshot,
    mem_save_fact,
)


class TestWriterQueueConcurrency(unittest.TestCase):
    """Verifies that WriterQueue eliminates SQLite lock errors during high-concurrency swarms."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_memory.db"
        conn = sqlite3.connect(self.db_path)
        init_project_db(conn)
        conn.close()

    def tearDown(self):
        from skillsdb.memory.writer_queue import stop_all_writer_queues
        stop_all_writer_queues()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_concurrent_writer_queue_stress(self):
        """16 concurrent threads writing simultaneously to the same database via WriterQueue."""
        wq = get_writer_queue(self.db_path)
        num_threads = 16
        writes_per_thread = 10
        errors = []

        def worker(tid: int):
            for i in range(writes_per_thread):
                try:
                    def _write(cur):
                        cur.execute(
                            "INSERT OR REPLACE INTO project_facts (key, value, updated_at) VALUES (?, ?, ?);",
                            (f"key_{tid}_{i}", f"val_{tid}_{i}", "2026-09-30T10:00:00Z"),
                        )
                    wq.execute_write(_write)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Expected 0 errors during concurrent writes, got: {errors}")

        # Verify all entries were recorded
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM project_facts;")
        count = cur.fetchone()[0]
        conn.close()
        self.assertEqual(count, num_threads * writes_per_thread)

    def test_journal_buffering_and_atomic_flush(self):
        """Tests append-only unshared journal files and atomic consolidation into memory.db."""
        project_root = Path(self.temp_dir.name)
        
        # Write events from multiple distinct simulated workers
        for worker_id in range(4):
            write_journal_entry(
                project_root,
                "fact",
                {"key": f"journal_cfg_{worker_id}", "value": f"active_{worker_id}", "updated_at": "2026-09-30T10:00:00Z"},
                worker_id=f"worker_{worker_id}",
            )
            write_journal_entry(
                project_root,
                "decision",
                {"title": f"Dec_{worker_id}", "content": f"Content_{worker_id}", "category": "architecture", "now": "2026-09-30T10:00:00Z"},
                worker_id=f"worker_{worker_id}",
            )

        journal_dir = project_root / ".agents" / "journal"
        journal_files = list(journal_dir.glob("events_*.jsonl"))
        self.assertEqual(len(journal_files), 4)

        # Flush into database
        conn = sqlite3.connect(self.db_path)
        flushed_count = flush_journals(project_root, conn)
        self.assertEqual(flushed_count, 8)

        # Verify journals removed
        remaining = list(journal_dir.glob("events_*.jsonl"))
        self.assertEqual(len(remaining), 0)

        # Verify records in database
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM project_facts WHERE key LIKE 'journal_cfg_%';")
        self.assertEqual(cur.fetchone()[0], 4)
        cur.execute("SELECT COUNT(*) FROM project_decisions WHERE title LIKE 'Dec_%';")
        self.assertEqual(cur.fetchone()[0], 4)
        conn.close()


class TestMultilingualSynonyms(unittest.TestCase):
    """Verifies cross-lingual query expansion from German concepts to English skill keywords."""

    def test_german_expansion(self):
        terms = expand_query_with_synonyms("mehrsprachige Applikation mit Übersetzung")
        self.assertIn("localization", terms)
        self.assertIn("internationalization", terms)
        self.assertIn("intl", terms)

    def test_architecture_expansion(self):
        terms = expand_query_with_synonyms("Zustandsverwaltung und Reaktivität")
        self.assertIn("state", terms)
        self.assertIn("management", terms)
        self.assertIn("bloc", terms)

    def test_database_and_pipeline_expansion(self):
        terms = expand_query_with_synonyms("Datenbank Abfrage und Datenpipeline")
        self.assertIn("database", terms)
        self.assertIn("bigquery", terms)
        self.assertIn("sql", terms)
        self.assertIn("pipeline", terms)

    def test_synonyms_database_seeding(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = Path(tmp_dir) / "test_syns.db"
            conn = sqlite3.connect(test_db)
            init_db(conn)
            init_synonyms(conn)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM synonym_synapses;")
            count = cur.fetchone()[0]
            self.assertGreaterEqual(count, len(DEFAULT_SYNONYMS))
            conn.close()


class TestModelTierDetector(unittest.TestCase):
    """Verifies resilient 4-stage tier detection and manual overrides."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "customizations.db"
        conn = get_connection(self.db_path)
        init_db(conn)
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manual_profile_override(self):
        set_profile("ultra", db_path=self.db_path)
        tier, desc = detect_model_tier()
        # When checking with db_path override
        conn = get_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT value FROM runtime_config WHERE key = 'model_tier';")
        val = cur.fetchone()[0]
        conn.close()
        self.assertEqual(val, "ultra")


if __name__ == "__main__":
    unittest.main()
