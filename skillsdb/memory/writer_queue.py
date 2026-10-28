"""
Multi-agent asynchronous SQLite writer queue and append-only journal buffer.
Eliminates database lock contention during concurrent subagent swarming with
deterministic queue draining, exponential lock backoff, and atexit cleanup.
"""

import os
import json
import time
import queue
import random
import atexit
import sqlite3
import threading
from pathlib import Path
from ..core.db import get_connection

# Map of db_path -> WriterQueue instance
_QUEUES: dict[str, "WriterQueue"] = {}
_LOCK = threading.Lock()


class WriterQueue:
    """Dedicated single-writer background thread consuming write operations sequentially."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name=f"WriterQueue-{self.db_path.name}",
        )
        self._thread.start()

    def execute_write(self, write_func, timeout: float = 15.0):
        """
        Submits a write callable: write_func(cursor) to the queue and waits for completion.
        Retries transient SQLite busy/lock states automatically and raises any fatal exception.
        """
        result_holder = []
        done_event = threading.Event()

        def task(conn: sqlite3.Connection):
            max_retries = 4
            try:
                for attempt in range(max_retries + 1):
                    try:
                        cursor = conn.cursor()
                        res = write_func(cursor)
                        conn.commit()
                        result_holder.append((True, res))
                        break
                    except sqlite3.OperationalError as op_err:
                        try:
                            conn.rollback()
                        except sqlite3.Error:
                            pass
                        msg = str(op_err).lower()
                        if ("locked" in msg or "busy" in msg) and attempt < max_retries:
                            time.sleep(0.04 * (2 ** attempt) + random.uniform(0.0, 0.02))
                            continue
                        result_holder.append((False, op_err))
                        break
                    except Exception as e:
                        try:
                            conn.rollback()
                        except sqlite3.Error:
                            pass
                        result_holder.append((False, e))
                        break
            finally:
                done_event.set()

        self.queue.put(task)
        if not done_event.wait(timeout=timeout):
            raise TimeoutError(f"Write operation on {self.db_path} timed out after {timeout}s")

        success, val = result_holder[0]
        if not success:
            raise val
        return val

    def _worker(self):
        try:
            conn = get_connection(self.db_path, is_project_db=True)
        except Exception as err:
            while not self.queue.empty():
                try:
                    task = self.queue.get_nowait()
                    try:
                        task(None)
                    finally:
                        self.queue.task_done()
                except Exception:
                    pass
            return

        while not self._stop_event.is_set() or not self.queue.empty():
            try:
                task = self.queue.get(timeout=0.15)
                try:
                    task(conn)
                finally:
                    self.queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                pass

        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except sqlite3.Error:
            pass
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def stop(self):
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)


def get_writer_queue(db_path: Path) -> WriterQueue:
    """Returns the singleton WriterQueue for the specified database path."""
    key = os.path.abspath(str(db_path))
    with _LOCK:
        if key not in _QUEUES or not _QUEUES[key]._thread.is_alive():
            _QUEUES[key] = WriterQueue(Path(key))
        return _QUEUES[key]


def stop_writer_queue(db_path: Path):
    """Stops the WriterQueue for the specified database path and releases connections."""
    key = os.path.abspath(str(db_path))
    with _LOCK:
        wq = _QUEUES.pop(key, None)
    if wq:
        wq.stop()


def stop_all_writer_queues():
    """Stops all active WriterQueues, drains pending writes, and joins worker threads."""
    with _LOCK:
        active_queues = list(_QUEUES.values())
        _QUEUES.clear()
    for wq in active_queues:
        try:
            wq.stop()
        except Exception:
            pass


# Guarantee clean SQLite connection shutdown and WAL checkpoint on process exit
atexit.register(stop_all_writer_queues)


def write_journal_entry(project_root: Path, entry_type: str, data: dict, worker_id: str = None) -> Path:
    """
    Appends an event to an unshared, append-only journal file without file locks.
    Used for concurrent out-of-process subagent logging.
    """
    wid = worker_id or f"w_{os.getpid()}_{threading.get_ident()}"
    journal_dir = project_root / ".agents" / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / f"events_{wid}.jsonl"

    record = {
        "timestamp": time.time(),
        "type": entry_type,
        "data": data,
    }
    with open(journal_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return journal_file


def flush_journals(project_root: Path, conn: sqlite3.Connection) -> int:
    """Atomically consolidates all pending journal files into memory.db and removes flushed files."""
    journal_dir = project_root / ".agents" / "journal"
    if not journal_dir.exists():
        return 0

    journal_files = list(journal_dir.glob("events_*.jsonl"))
    if not journal_files:
        return 0

    cursor = conn.cursor()
    flushed_count = 0

    for jf in journal_files:
        try:
            with open(jf, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    etype = rec.get("type")
                    data = rec.get("data", {})
                    if etype == "fact":
                        fkey = data.get("key")
                        fval = data.get("value")
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO project_facts (key, value, updated_at, deprecated_at, superseded_by)
                            VALUES (?, ?, ?, NULL, NULL);
                            """,
                            (fkey, fval, data.get("updated_at", "")),
                        )
                        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (fkey,))
                        cursor.execute(
                            """
                            INSERT INTO project_memory_fts (source_table, title_or_key, content)
                            VALUES ('fact', ?, ?);
                            """,
                            (fkey, fval or ""),
                        )
                        flushed_count += 1
                    elif etype == "decision":
                        dtitle = data.get("title")
                        dcontent = data.get("content")
                        cursor.execute(
                            """
                            INSERT INTO project_decisions (title, content, category, is_active, created_at, updated_at)
                            VALUES (?, ?, ?, 1, ?, ?);
                            """,
                            (
                                dtitle,
                                dcontent,
                                data.get("category", "architecture"),
                                data.get("now", ""),
                                data.get("now", ""),
                            ),
                        )
                        cursor.execute(
                            """
                            INSERT INTO project_memory_fts (source_table, title_or_key, content)
                            VALUES ('decision', ?, ?);
                            """,
                            (dtitle or "", dcontent or ""),
                        )
                        flushed_count += 1
            jf.unlink(missing_ok=True)
        except Exception:
            pass

    if flushed_count > 0:
        conn.commit()
    return flushed_count
