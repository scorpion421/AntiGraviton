"""
Multi-agent asynchronous SQLite writer queue and append-only journal buffer.
Eliminates database lock contention during concurrent subagent swarming.
"""

import os
import json
import time
import queue
import sqlite3
import threading
from pathlib import Path

# Map of db_path -> WriterQueue instance
_QUEUES: dict[str, "WriterQueue"] = {}
_LOCK = threading.Lock()


class WriterQueue:
    """Dedicated single-writer background thread consuming write operations sequentially."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True, name=f"WriterQueue-{self.db_path.name}")
        self._thread.start()

    def execute_write(self, write_func, timeout: float = 10.0):
        """
        Submits a write callable: write_func(cursor) to the queue and waits for completion.
        Raises any exception raised by write_func.
        """
        result_holder = []
        done_event = threading.Event()

        def task(conn):
            try:
                cursor = conn.cursor()
                res = write_func(cursor)
                conn.commit()
                result_holder.append((True, res))
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                result_holder.append((False, e))
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
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")

        while not self._stop_event.is_set():
            try:
                task = self.queue.get(timeout=0.2)
                task(conn)
                self.queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                pass

        try:
            conn.close()
        except Exception:
            pass

    def stop(self):
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


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
    """Stops all active WriterQueues and joins worker threads."""
    with _LOCK:
        for wq in list(_QUEUES.values()):
            try:
                wq.stop()
            except Exception:
                pass
        _QUEUES.clear()


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
        "data": data
    }
    with open(journal_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return journal_file


def flush_journals(project_root: Path, conn: sqlite3.Connection):
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
                        cursor.execute("""
                        INSERT OR REPLACE INTO project_facts (key, value, updated_at)
                        VALUES (?, ?, ?);
                        """, (data.get("key"), data.get("value"), data.get("updated_at", "")))
                        flushed_count += 1
                    elif etype == "decision":
                        cursor.execute("""
                        INSERT INTO project_decisions (title, content, category, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, 1, ?, ?);
                        """, (data.get("title"), data.get("content"), data.get("category", "architecture"), data.get("now", ""), data.get("now", "")))
                        flushed_count += 1
            jf.unlink(missing_ok=True)
        except Exception:
            pass

    conn.commit()
    return flushed_count
