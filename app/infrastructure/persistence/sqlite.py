import json
import sqlite3
from pathlib import Path

from ...domain.models import QCRun


class SqliteRunStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "agent-qc.sqlite3"
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY, received_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
            """)

    def _connect(self):
        return sqlite3.connect(self.path)

    def claim_delivery(self, delivery_id: str) -> bool:
        try:
            with self._connect() as db:
                db.execute("INSERT INTO deliveries(delivery_id) VALUES (?)", (delivery_id,))
            return True
        except sqlite3.IntegrityError:
            return False

    def save_run(self, run: QCRun) -> None:
        payload = run.model_dump_json()
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO runs(run_id, payload) VALUES (?, ?)",
                (run.run_id, payload),
            )

    def get_run(self, run_id: str) -> QCRun | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return QCRun.model_validate(json.loads(row[0])) if row else None


# Compatibility for callers that imported the original demo name.
Store = SqliteRunStore
