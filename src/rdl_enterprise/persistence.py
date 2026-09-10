"""Small SQLite-backed persistence boundary for the Enterprise runtime."""

from __future__ import annotations

import pickle
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from typing import List, Optional, Tuple


class SQLiteCaseStore:
    """Persist trusted in-process case snapshots and append-only audit events."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str):
        self.path = path
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_records (
                    ticket_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    snapshot BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT,
                    event_type TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_operations (
                    operation_id TEXT PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    ticket_id TEXT,
                    result BLOB NOT NULL,
                    committed_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_metadata(key, value) VALUES('version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
            version = db.execute("SELECT value FROM schema_metadata WHERE key = 'version'").fetchone()[0]
            if version != str(self.SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported case store schema version: {version}")

    def save_runtime_state(self, state: object) -> None:
        payload = sqlite3.Binary(pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL))
        with self._connect() as db:
            db.execute(
                """INSERT INTO schema_metadata(key, value) VALUES('runtime_state', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (payload, ),
            )

    def load_runtime_state(self) -> Optional[object]:
        with self._connect() as db:
            row = db.execute("SELECT value FROM schema_metadata WHERE key = 'runtime_state'").fetchone()
        return pickle.loads(row[0]) if row else None

    def save_case(self, ticket_id: str, snapshot: object, status: str) -> None:
        payload = sqlite3.Binary(pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL))
        with self._connect() as db:
            db.execute(
                """INSERT INTO case_records(ticket_id, status, snapshot, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(ticket_id) DO UPDATE SET status=excluded.status,
                   snapshot=excluded.snapshot, updated_at=excluded.updated_at""",
                (ticket_id, status, payload, datetime.utcnow().isoformat()),
            )

    def load_pending(self) -> List[Tuple[str, object]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT ticket_id, snapshot FROM case_records WHERE status = 'pending' ORDER BY ticket_id"
            ).fetchall()
        return [(ticket_id, pickle.loads(snapshot)) for ticket_id, snapshot in rows]

    def load_case(self, ticket_id: str) -> Optional[object]:
        with self._connect() as db:
            row = db.execute("SELECT snapshot FROM case_records WHERE ticket_id = ?", (ticket_id,)).fetchone()
        return pickle.loads(row[0]) if row else None

    def load_resolved(self) -> List[object]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT snapshot FROM case_records WHERE status != 'pending' ORDER BY updated_at, ticket_id"
            ).fetchall()
        return [pickle.loads(snapshot) for (snapshot,) in rows]

    def record_event(self, event_type: str, ticket_id: Optional[str] = None, payload: object = None) -> None:
        blob = sqlite3.Binary(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
        with self._connect() as db:
            db.execute(
                "INSERT INTO audit_events(ticket_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (ticket_id, event_type, blob, datetime.utcnow().isoformat()),
            )

    def load_operation(self, operation_id: str) -> Optional[object]:
        with self._connect() as db:
            row = db.execute("SELECT result FROM processed_operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return pickle.loads(row[0]) if row else None

    def get_operation(self, operation_id: str):
        with self._connect() as db:
            row = db.execute(
                "SELECT operation_type, ticket_id, result FROM processed_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if not row:
            return None
        return row[0], row[1], pickle.loads(row[2])

    def commit_transition(self, ticket_id: str, snapshot: object, state: object,
                          event_payload: object, operation_id: Optional[str] = None,
                          result: object = None) -> bool:
        now = datetime.utcnow().isoformat()
        with self._connect() as db:
            if operation_id and db.execute("SELECT 1 FROM processed_operations WHERE operation_id = ?", (operation_id,)).fetchone():
                return False
            blobs = [sqlite3.Binary(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)) for value in (snapshot, state, event_payload, result)]
            db.execute("INSERT INTO case_records(ticket_id, status, snapshot, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(ticket_id) DO UPDATE SET status=excluded.status, snapshot=excluded.snapshot, updated_at=excluded.updated_at", (ticket_id, snapshot.status.value, blobs[0], now))
            db.execute("INSERT INTO audit_events(ticket_id, event_type, payload, created_at) VALUES (?, 'ticket_resolved', ?, ?)", (ticket_id, blobs[2], now))
            db.execute("INSERT INTO schema_metadata(key, value) VALUES('runtime_state', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (blobs[1],))
            if operation_id:
                db.execute("INSERT INTO processed_operations VALUES (?, 'ticket_resolved', ?, ?, ?)", (operation_id, ticket_id, blobs[3], now))
        return True
