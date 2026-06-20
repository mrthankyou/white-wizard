"""SQLite state layer for White Wizard.

All runtime/operational data lives in .wizard/state.db in the project directory.
Config (stream questions, orchestration plans) stays in wizard.yaml.
"""
import os
import sqlite3


def _db_path():
    wizard_dir = os.path.join(os.getcwd(), ".wizard")
    os.makedirs(wizard_dir, exist_ok=True)
    return os.path.join(wizard_dir, "state.db")


def _connect():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stream_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                commit_hash TEXT,
                git_diff    TEXT
            );

            CREATE TABLE IF NOT EXISTS stream_findings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES stream_runs(id),
                question_id TEXT NOT NULL,
                priority    INTEGER NOT NULL,
                response    TEXT NOT NULL,
                approved    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            );
        """)


def start_stream_run(commit_hash, git_diff):
    """Insert a new stream run row and return its id."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO stream_runs (started_at, commit_hash, git_diff) "
            "VALUES (datetime('now'), ?, ?)",
            (commit_hash, git_diff),
        )
        return cur.lastrowid


def finish_stream_run(run_id, commit_hash):
    """Mark a run as finished and record the scanned commit."""
    with _connect() as conn:
        conn.execute(
            "UPDATE stream_runs SET finished_at = datetime('now'), commit_hash = ? "
            "WHERE id = ?",
            (commit_hash, run_id),
        )


def save_finding(run_id, question_id, priority, response, approved):
    """Persist a stream finding."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO stream_findings "
            "(run_id, question_id, priority, response, approved, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (run_id, question_id, priority, response, int(approved)),
        )


def get_last_scanned_commit():
    """Return the commit hash from the most recent finished stream run, or None."""
    try:
        init_db()
        with _connect() as conn:
            row = conn.execute(
                "SELECT commit_hash FROM stream_runs "
                "WHERE finished_at IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            return row["commit_hash"] if row else None
    except Exception:
        return None


def get_stream_stats():
    """Return a dict with summary stats for display."""
    try:
        init_db()
        with _connect() as conn:
            runs = conn.execute("SELECT COUNT(*) FROM stream_runs WHERE finished_at IS NOT NULL").fetchone()[0]
            findings = conn.execute("SELECT COUNT(*) FROM stream_findings WHERE approved = 1").fetchone()[0]
            last = conn.execute(
                "SELECT finished_at, commit_hash FROM stream_runs "
                "WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            return {
                "total_runs": runs,
                "approved_findings": findings,
                "last_run_at": last["finished_at"] if last else None,
                "last_commit": last["commit_hash"] if last else None,
            }
    except Exception:
        return {"total_runs": 0, "approved_findings": 0, "last_run_at": None, "last_commit": None}
