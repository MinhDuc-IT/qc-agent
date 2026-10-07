import json
import sqlite3
from pathlib import Path

from ...domain.models import DecisionLog, FlakyTestRecord, QCRun, RunComparison


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
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    component TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS flaky_tests (
                    fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_comparisons (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_queue (
                    run_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, enqueued_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                UPDATE run_queue SET status = 'queued' WHERE status = 'running';
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
            for result in run.results:
                db.execute(
                    "INSERT OR REPLACE INTO tasks(task_id, run_id, payload) VALUES (?, ?, ?)",
                    (result.task_id, run.run_id, result.model_dump_json()),
                )
                for finding in result.findings:
                    db.execute(
                        "INSERT OR REPLACE INTO findings(finding_id, task_id, payload) VALUES (?, ?, ?)",
                        (finding.id, result.task_id, finding.model_dump_json()),
                    )

    def get_run(self, run_id: str) -> QCRun | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return QCRun.model_validate(json.loads(row[0])) if row else None

    def cancel_stale_runs(self, repository: str, new_head_sha: str,
                          pull_request_number: int | None = None) -> list[str]:
        cancelled: list[str] = []
        with self._connect() as db:
            rows = db.execute("SELECT run_id, payload FROM runs").fetchall()
            for run_id, payload in rows:
                run = QCRun.model_validate_json(payload)
                existing_pr = run.trigger_context.pull_request
                same_change = (pull_request_number is None or
                               (existing_pr is not None and
                                existing_pr.number == pull_request_number))
                if (run.trigger_context.repository.full_name == repository
                        and same_change
                        and run.trigger_context.revision.head_sha != new_head_sha
                        and run.status in {"queued", "preparing", "running"}):
                    run.status = "cancelled"
                    run.verdict = "unknown"
                    run.error = "STALE_RUN_CANCELLED"
                    db.execute("UPDATE runs SET payload = ? WHERE run_id = ?",
                               (run.model_dump_json(), run_id))
                    cancelled.append(run_id)
                    db.execute("DELETE FROM run_queue WHERE run_id = ?", (run_id,))
        return cancelled

    def enqueue_run(self, run_id: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO run_queue(run_id) VALUES (?)", (run_id,))

    def claim_next_run(self) -> str | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT run_id FROM run_queue WHERE status = 'queued' "
                "ORDER BY enqueued_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE run_queue SET status = 'running', attempts = attempts + 1 "
                       "WHERE run_id = ?", (row[0],))
            return str(row[0])

    def complete_queued_run(self, run_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM run_queue WHERE run_id = ?", (run_id,))

    def append_decision(self, decision: DecisionLog) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO decisions(decision_id, run_id, component, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (decision.decision_id, decision.run_id, decision.component,
                 decision.model_dump_json(), decision.created_at.isoformat()),
            )

    def save_flaky_test(self, record: FlakyTestRecord) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO flaky_tests(fingerprint, payload) VALUES (?, ?)",
                       (record.fingerprint, record.model_dump_json()))

    def get_flaky_test(self, fingerprint: str) -> FlakyTestRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM flaky_tests WHERE fingerprint = ?",
                             (fingerprint,)).fetchone()
        return FlakyTestRecord.model_validate_json(row[0]) if row else None

    def save_comparison(self, comparison: RunComparison) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO run_comparisons(run_id, payload) VALUES (?, ?)",
                       (comparison.run_id, comparison.model_dump_json()))


# Compatibility for callers that imported the original demo name.
Store = SqliteRunStore
