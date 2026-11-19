"""SQLite checkpoints. A write transaction encloses each read-only tool attempt."""

import json
import sqlite3
from contextlib import contextmanager


class RunStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=120)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, run_id):
        row = self.db.execute(
            "SELECT payload FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def save(self, run):
        if not self.db.in_transaction:
            raise RuntimeError("checkpoint requires transaction")
        self.db.execute(
            "INSERT INTO runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (run["run_id"], json.dumps(run, ensure_ascii=False, separators=(",", ":"))),
        )
