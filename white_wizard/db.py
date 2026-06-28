"""SQLite state layer for White Wizard.

All runtime/operational data lives in .wizard/state.db in the project directory.
The wizard's own config (settings, stream thoughts) stays in wizard.yaml; the
orchestration itself lives in the AI's native store (.claude/ for Claude).
"""
import os
import sqlite3
from contextlib import contextmanager


def _db_path():
    wizard_dir = os.path.join(os.getcwd(), ".wizard")
    os.makedirs(wizard_dir, exist_ok=True)
    return os.path.join(wizard_dir, "state.db")


def _connect():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _session():
    """Yield a connection that commits on success and is always closed.

    sqlite3's own ``with conn:`` only manages the transaction (commit/rollback)
    — it never closes the connection, which leaked a handle on every call. This
    wraps that transaction context and guarantees ``close()`` runs.
    """
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with _session() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                orch_folder    TEXT NOT NULL,
                description    TEXT NOT NULL,
                task_type      TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending',
                is_user_prompt INTEGER NOT NULL DEFAULT 0,
                summary        TEXT NOT NULL DEFAULT '',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stream_config (
                orch_folder     TEXT PRIMARY KEY,
                enabled         INTEGER NOT NULL DEFAULT 1
            );
        """)


# ---------------------------------------------------------------------------
# Stream config helpers
# ---------------------------------------------------------------------------

def ensure_stream_config(orch_folder):
    """Create a stream_config row for orch_folder if one doesn't exist."""
    init_db()
    with _session() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO stream_config (orch_folder) VALUES (?)",
            (orch_folder,),
        )


def is_stream_enabled(orch_folder):
    """Return True if streaming is enabled for this orch (default True if no row)."""
    try:
        init_db()
        with _session() as conn:
            row = conn.execute(
                "SELECT enabled FROM stream_config WHERE orch_folder = ?",
                (orch_folder,),
            ).fetchone()
            return bool(row["enabled"]) if row else True
    except Exception:
        return True


def set_stream_enabled(orch_folder, enabled):
    """Upsert the enabled flag for this orch."""
    init_db()
    with _session() as conn:
        conn.execute(
            "INSERT INTO stream_config (orch_folder, enabled) VALUES (?, ?) "
            "ON CONFLICT(orch_folder) DO UPDATE SET enabled = excluded.enabled",
            (orch_folder, int(enabled)),
        )


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def add_task(orch_folder, description, task_type, is_user_prompt=False):
    """Insert a new task and return its id."""
    init_db()
    with _session() as conn:
        cur = conn.execute(
            "INSERT INTO tasks "
            "(orch_folder, description, task_type, is_user_prompt, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (orch_folder, description, task_type, int(is_user_prompt)),
        )
        return cur.lastrowid


def get_next_pending_task(orch_folder):
    """Return the next pending task dict (user-prompt tasks first), or None."""
    try:
        init_db()
        with _session() as conn:
            row = conn.execute(
                "SELECT * FROM tasks "
                "WHERE orch_folder = ? AND status = 'pending' "
                "ORDER BY is_user_prompt DESC, id ASC LIMIT 1",
                (orch_folder,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def set_task_running(task_id):
    """Mark a task as running."""
    try:
        with _session() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
    except Exception:
        pass


def complete_task(task_id, summary=""):
    """Mark a task as done and store a brief summary."""
    try:
        with _session() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'done', summary = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (summary, task_id),
            )
    except Exception:
        pass


def fail_task(task_id, summary=""):
    """Mark a task as failed (it ran but didn't achieve its change) with a summary."""
    try:
        with _session() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'failed', summary = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (summary, task_id),
            )
    except Exception:
        pass


def delete_tasks_for_orch(orch_folder):
    """Delete all tasks for an orchestration; return how many rows were removed.
    Called when the orchestration is wiped."""
    try:
        with _session() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE orch_folder = ?", (orch_folder,))
            return cur.rowcount or 0
    except Exception:
        return 0


def delete_stream_config(orch_folder):
    """Delete an orchestration's stream_config row. Called when it's wiped."""
    try:
        with _session() as conn:
            conn.execute("DELETE FROM stream_config WHERE orch_folder = ?", (orch_folder,))
    except Exception:
        pass


def prune_orphaned_state(known_folders):
    """Delete tasks and stream_config for orchestrations not in ``known_folders``
    (e.g. wiped in an earlier session, before wipe cleaned the DB). Returns the
    number of task rows removed. Best-effort."""
    try:
        init_db()
        with _session() as conn:
            folders = {r["orch_folder"] for r in conn.execute(
                "SELECT DISTINCT orch_folder FROM tasks").fetchall()}
            folders |= {r["orch_folder"] for r in conn.execute(
                "SELECT orch_folder FROM stream_config").fetchall()}
            removed = 0
            for folder in folders - set(known_folders):
                cur = conn.execute("DELETE FROM tasks WHERE orch_folder = ?", (folder,))
                removed += cur.rowcount or 0
                conn.execute("DELETE FROM stream_config WHERE orch_folder = ?", (folder,))
            return removed
    except Exception:
        return 0


def get_tasks_for_orch(orch_folder, limit=500):
    """Return tasks for this orch ordered newest-first.

    The cap is generous (not just a screenful) so the right-pane scroller has the
    full recent history to move through — on a tall terminal a small cap meant
    everything fit at once and never scrolled, hiding older tasks entirely."""
    try:
        init_db()
        with _session() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE orch_folder = ? ORDER BY id DESC LIMIT ?",
                (orch_folder, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_all_tasks_grouped(limit_per_orch=15):
    """Return {orch_folder: [task_dict, ...]} for all orches, newest-first."""
    try:
        init_db()
        with _session() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY orch_folder, id DESC"
            ).fetchall()
            grouped = {}
            for row in rows:
                d = dict(row)
                folder = d["orch_folder"]
                if folder not in grouped:
                    grouped[folder] = []
                if len(grouped[folder]) < limit_per_orch:
                    grouped[folder].append(d)
            return grouped
    except Exception:
        return {}
