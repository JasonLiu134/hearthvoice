"""Temporary Builder 3 persistence; Builder 4 can implement the same load/save API."""

import json
import sqlite3
from pathlib import Path


class SQLiteStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS orchestration (id INTEGER PRIMARY KEY, state TEXT NOT NULL)"
        )
        self.connection.commit()

    def load(self):
        row = self.connection.execute("SELECT state FROM orchestration WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {
            "profiles": {}, "check_ins": {}, "calls": {}, "alerts": {}, "events": {}
        }

    def save(self, state):
        with self.connection:
            self.connection.execute(
                "INSERT INTO orchestration VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                (json.dumps(state),),
            )

    def close(self):
        self.connection.close()
