import sqlite3
from contextlib import contextmanager
from config import DB_PATH


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_records (
                watcher_id  TEXT NOT NULL,
                record_id   TEXT NOT NULL,
                seen_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (watcher_id, record_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_ids (
                chat_id   INTEGER PRIMARY KEY,
                username  TEXT,
                added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


# ── seen-record helpers ───────────────────────────────────────

def get_all_seen(watcher_id: str) -> set[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT record_id FROM seen_records WHERE watcher_id = ?",
            (watcher_id,),
        ).fetchall()
    return {r["record_id"] for r in rows}


def mark_seen(watcher_id: str, record_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_records (watcher_id, record_id) VALUES (?, ?)",
            (watcher_id, record_id),
        )


# ── chat-id helpers ───────────────────────────────────────────

def add_chat_id(chat_id: int, username: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chat_ids (chat_id, username) VALUES (?, ?)",
            (chat_id, username),
        )


def remove_chat_id(chat_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM chat_ids WHERE chat_id = ?", (chat_id,))


def get_chat_ids() -> list[int]:
    with _conn() as conn:
        rows = conn.execute("SELECT chat_id FROM chat_ids").fetchall()
    return [r["chat_id"] for r in rows]
