import os
import json
import uuid
import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  requester TEXT NOT NULL,
  status TEXT NOT NULL,
  kind TEXT,
  target_folder TEXT,
  temp_dir TEXT NOT NULL,
  total INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0,
  failed INTEGER DEFAULT 0,
  skipped INTEGER DEFAULT 0,
  error TEXT,
  failures TEXT,
  label TEXT,
  dry_run INTEGER DEFAULT 0,
  keep_files TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

# Migrations for upgrading older DBs. Each statement runs in its own try/except
# so already-applied changes are silently skipped.
_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN skipped INTEGER DEFAULT 0",  # v1.1
    "ALTER TABLE tasks ADD COLUMN label TEXT",                 # v1.3
    "ALTER TABLE tasks ADD COLUMN dry_run INTEGER DEFAULT 0",  # v1.3
    "ALTER TABLE tasks ADD COLUMN keep_files TEXT",            # v1.3
]


def _now():
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, db_path):
        self.db_path = db_path
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        conn = self._conn()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            for stmt in _MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # 列已存在
            conn.commit()
        finally:
            conn.close()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def create_task(self, account, requester, target_folder, temp_dir,
                    label=None, keep_files=None):
        """keep_files: optional list of basenames to keep during processing
        (used by `retry only_failed` to re-run just the previously failed
        files). Stored as JSON; worker honors it."""
        tid = uuid.uuid4().hex
        ts = _now()
        keep_json = json.dumps(keep_files) if keep_files else None
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO tasks (id, account, requester, status, "
                "target_folder, temp_dir, label, keep_files, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tid, account, requester, "queued", target_folder,
                 temp_dir, label, keep_json, ts, ts))
            conn.commit()
        finally:
            conn.close()
        return tid

    def get_task(self, task_id):
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id=?",
                               (task_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_tasks(self, requester, limit=200):
        """Latest `limit` tasks for the requester (newest first). Capped to
        keep responses bounded; users running ZImport-tools for a year would
        otherwise download thousands of rows on every page refresh."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE requester=? "
                "ORDER BY created_at DESC LIMIT ?",
                (requester, int(limit))).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def claim_next(self):
        conn = self._conn()
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tasks WHERE status='queued' "
                "ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute("UPDATE tasks SET status='running', updated_at=? "
                         "WHERE id=?", (_now(), row["id"]))
            conn.execute("COMMIT")
            return dict(row)
        finally:
            conn.close()

    def set_totals(self, task_id, total):
        self._update(task_id, {"total": total})

    def update_progress(self, task_id, done, failed, skipped=None):
        fields = {"done": done, "failed": failed}
        if skipped is not None:
            fields["skipped"] = skipped
        self._update(task_id, fields)

    def set_failures(self, task_id, failures):
        self._update(task_id, {"failures": json.dumps(failures,
                                                      ensure_ascii=False)})

    def set_status(self, task_id, status, error=None, kind=None):
        fields = {"status": status}
        if error is not None:
            fields["error"] = error
        if kind is not None:
            fields["kind"] = kind
        self._update(task_id, fields)

    def request_cancel(self, task_id):
        """Mark a queued/running task as cancel-requested. The worker
        polls task status each iteration and stops at the next check.
        Returns the new status, or None if the task is not cancellable."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT status FROM tasks WHERE id=?",
                (task_id,)).fetchone()
            if row is None:
                return None
            if row["status"] not in ("queued", "running"):
                return None
            # Queued tasks go straight to cancelled (worker hasn't started).
            # Running tasks transition to cancelling; worker will flip to
            # cancelled when it reaches the next check.
            new_status = ("cancelled" if row["status"] == "queued"
                          else "cancelling")
            conn.execute("UPDATE tasks SET status=?, updated_at=? "
                         "WHERE id=?", (new_status, _now(), task_id))
            conn.commit()
            return new_status
        finally:
            conn.close()

    def cancel_requested(self, task_id):
        """True if user asked to cancel a running task."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT status FROM tasks WHERE id=?",
                               (task_id,)).fetchone()
            return row is not None and row["status"] == "cancelling"
        finally:
            conn.close()

    def count_active(self):
        conn = self._conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE status IN ('queued','running')").fetchone()[0]
        finally:
            conn.close()

    def recover_interrupted(self):
        """On worker startup, sweep up tasks left in an in-progress state by
        the previous process exit (crash, SIGTERM, OOM, …). Without this:
          - 'running'    -> task displays as running forever; user can't retry.
          - 'cancelling' -> user cancelled but worker died before flipping to
                            cancelled; task hangs indefinitely and purge_old
                            never sweeps it.
        Both become 'interrupted', which is retriable and purge_old-eligible.
        """
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE tasks SET status='interrupted', updated_at=? "
                "WHERE status IN ('running', 'cancelling')", (_now(),))
            conn.commit()
        finally:
            conn.close()

    def purge_old(self, retention_days):
        """Delete finished/failed/interrupted task rows older than
        retention_days. Returns the list of temp_dir paths of deleted
        tasks so the caller can remove them from disk."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=retention_days)).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT temp_dir FROM tasks WHERE updated_at < ? "
                "AND status IN ('done','failed','interrupted','cancelled')",
                (cutoff,)).fetchall()
            conn.execute(
                "DELETE FROM tasks WHERE updated_at < ? "
                "AND status IN ('done','failed','interrupted','cancelled')",
                (cutoff,))
            conn.commit()
            return [r["temp_dir"] for r in rows]
        finally:
            conn.close()

    def _update(self, task_id, fields):
        fields = dict(fields)
        fields["updated_at"] = _now()
        cols = ", ".join("%s=?" % k for k in fields)
        conn = self._conn()
        try:
            conn.execute("UPDATE tasks SET %s WHERE id=?" % cols,
                         list(fields.values()) + [task_id])
            conn.commit()
        finally:
            conn.close()
