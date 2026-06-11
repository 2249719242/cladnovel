#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from .story_contracts import StoryContractPaths, read_json_if_exists, write_json
from .story_event_schema import StoryEvent


class EventLogStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = StoryContractPaths.from_project_root(self.project_root)

    @contextmanager
    def _connect(self, *, row_factory: bool = False) -> Iterator[sqlite3.Connection]:
        """统一 SQLite 连接管理，确保连接始终关闭。"""
        db_path = self.project_root / ".webnovel" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        if row_factory:
            conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def write_events(self, chapter: int, events: List[dict]) -> Path:
        normalized = self._normalize_events(chapter, events)
        path = self.paths.event_json(chapter)
        write_json(path, normalized)
        self._write_sqlite_mirror(normalized)
        return path

    def read_events(self, chapter: int) -> List[Dict[str, Any]]:
        return list(read_json_if_exists(self.paths.event_json(chapter)) or [])

    def list_recent(self, chapter: int | None = None, limit: int = 200) -> List[Dict[str, Any]]:
        db_path = self.project_root / ".webnovel" / "index.db"
        if not db_path.is_file():
            return []
        with self._connect(row_factory=True) as conn:
            try:
                if chapter is not None:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        WHERE chapter = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (chapter, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        ORDER BY chapter DESC, id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
            except sqlite3.OperationalError:
                return []

        result: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "chapter": row["chapter"],
                    "event_type": row["event_type"],
                    "subject": row["subject"],
                    "payload": payload,
                }
            )
        return result

    def health(self) -> Dict[str, Any]:
        db_path = self.project_root / ".webnovel" / "index.db"
        file_count = len(list(self.paths.events_dir.glob("chapter_*.events.json")))
        sqlite_rows = 0
        if db_path.is_file():
            with self._connect() as conn:
                try:
                    sqlite_rows = int(
                        conn.execute("SELECT COUNT(*) FROM story_events").fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    sqlite_rows = 0
        return {"ok": True, "sqlite_rows": sqlite_rows, "event_files": file_count}

    @staticmethod
    def _derive_event_id(payload: Dict[str, Any]) -> str:
        """缺失 event_id 时按事件内容生成确定性 ID。

        确定性（而非随机 UUID）保证 chapter-commit 重跑时 SQLite 镜像的
        `INSERT OR IGNORE ... event_id UNIQUE` 幂等，不会重复入库。
        """
        material = json.dumps(
            {
                "chapter": payload.get("chapter"),
                "event_type": payload.get("event_type"),
                "subject": payload.get("subject"),
                "payload": payload.get("payload"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
        return f"evt_{payload.get('chapter', 0)}_{digest}"

    def _normalize_events(self, chapter: int, events: List[dict]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            payload = dict(event)
            payload["chapter"] = int(payload.get("chapter") or chapter)
            if not str(payload.get("event_id") or "").strip():
                payload["event_id"] = self._derive_event_id(payload)
            normalized.append(StoryEvent.model_validate(payload).model_dump())
        return normalized

    def _write_sqlite_mirror(self, events: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS story_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    chapter INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_chapter ON story_events(chapter)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_type ON story_events(event_type)"
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO story_events(event_id, chapter, event_type, subject, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        event["event_id"],
                        int(event["chapter"]),
                        event["event_type"],
                        event["subject"],
                        json.dumps(event.get("payload") or {}, ensure_ascii=False),
                    )
                    for event in events
                ],
            )
            conn.commit()
