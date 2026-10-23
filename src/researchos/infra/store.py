"""Single-process SQLite repository; JSON snapshots plus queryable evidence and event log."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from researchos.domain.models import Run, SourceRecord, utcnow


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(
                    run_id TEXT, id TEXT, body TEXT, PRIMARY KEY(run_id,id));
                CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(run_id UNINDEXED,
                    source_id UNINDEXED, title, excerpt);
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
                    kind TEXT, at TEXT, payload TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, run: Run) -> None:
        run.updated_at = utcnow()
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO runs VALUES (?,?)", (run.id, run.model_dump_json()))
            db.execute("DELETE FROM sources WHERE run_id=?", (run.id,))
            db.execute("DELETE FROM passages WHERE run_id=?", (run.id,))
            for s in run.sources:
                db.execute(
                    "INSERT INTO sources VALUES (?,?,?)", (run.id, s.id, s.model_dump_json())
                )
                db.execute(
                    "INSERT INTO passages VALUES (?,?,?,?)", (run.id, s.id, s.title, s.excerpt)
                )

    def get(self, run_id: str) -> Run | None:
        with self.connect() as db:
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        return Run.model_validate_json(row[0]) if row else None

    def list(self, limit: int = 50) -> list[Run]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT body FROM runs ORDER BY rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Run.model_validate_json(r[0]) for r in rows]

    def event(self, run_id: str, kind: str, **payload) -> int:
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO events(run_id,kind,at,payload) VALUES (?,?,?,?)",
                (run_id, kind, utcnow(), json.dumps(payload, ensure_ascii=False)),
            )
            return cur.lastrowid

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after)
            ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def search(self, run_id: str, query: str, limit: int = 5) -> list[SourceRecord]:
        tokens = re.findall(r"\w+", query)[:20]
        if not tokens:
            return []
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in tokens)
        with self.connect() as db:
            rows = db.execute(
                """SELECT s.body FROM passages p JOIN sources s
                ON p.run_id=s.run_id AND p.source_id=s.id
                WHERE passages MATCH ? AND p.run_id=? ORDER BY rank LIMIT ?""",
                (match, run_id, limit),
            ).fetchall()
            # unicode61 treats long Chinese phrases as a single token. Keep
            # FTS ranking for token matches, then support literal CJK passage
            # lookup when it found nothing; this is not semantic retrieval.
            if not rows and re.search(r"[\u3400-\u9fff]", query):
                phrase = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = db.execute(
                    """SELECT body FROM sources WHERE run_id=? AND
                    (json_extract(body, '$.title') LIKE ? ESCAPE '\\' OR
                     json_extract(body, '$.excerpt') LIKE ? ESCAPE '\\') LIMIT ?""",
                    (run_id, f"%{phrase}%", f"%{phrase}%", limit),
                ).fetchall()
        return [SourceRecord.model_validate_json(r[0]) for r in rows]
