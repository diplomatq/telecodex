from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import (
    CodexLaunchMode,
    ProjectActivitySummary,
    ProjectRun,
    ProjectRunStatus,
    ProjectSession,
)


class _AsyncCursor:
    def __init__(self, rows: list[sqlite3.Row], lastrowid: int = 0):
        self._rows = rows
        self._index = 0
        self._lastrowid = lastrowid

    @property
    def lastrowid(self) -> int:
        return self._lastrowid

    async def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    async def fetchall(self):
        if self._index >= len(self._rows):
            return []
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class _AsyncConnection:
    def __init__(self, db_path: Path):
        self._db_path = db_path

    @staticmethod
    def _open(db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _AsyncCursor:
        conn = self._open(self._db_path)
        try:
            cursor = conn.execute(sql, params)
            try:
                rows = cursor.fetchall() if cursor.description is not None else []
                lastrowid = int(cursor.lastrowid or 0)
            finally:
                cursor.close()
            conn.commit()
            return _AsyncCursor(rows, lastrowid)
        finally:
            conn.close()

    async def execute_fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        conn = self._open(self._db_path)
        try:
            cursor = conn.execute(sql, params)
            try:
                return cursor.fetchall()
            finally:
                cursor.close()
        finally:
            conn.close()

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        return None


class SessionStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: Optional[_AsyncConnection] = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = _AsyncConnection(self.db_path)
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        await self._initialize_schema_version()
        await self._run_migrations()
        await self.conn.commit()

    async def _initialize_schema_version(self) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
            """
        )

    async def _run_migrations(self) -> None:
        current_version = await self._get_schema_version()
        if current_version < 1:
            await self._migration_v1()
            await self._set_schema_version(1)
        if current_version < 2:
            await self._migration_v2()
            await self._set_schema_version(2)
        if current_version < 3:
            await self._migration_v3()
            await self._set_schema_version(3)
        if current_version < 4:
            await self._migration_v4()
            await self._set_schema_version(4)
        if current_version < 5:
            await self._migration_v5()
            await self._set_schema_version(5)
        if current_version < 6:
            await self._migration_v6()
            await self._set_schema_version(6)
        if current_version < 7:
            await self._migration_v7()
            await self._set_schema_version(7)

    async def _get_schema_version(self) -> int:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0

    async def _set_schema_version(self, version: int) -> None:
        conn = self._require_conn()
        await conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))

    async def _migration_v1(self) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_sessions (
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_status TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (user_id, project_path)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                project_path TEXT,
                event_type TEXT NOT NULL,
                event_status TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_sessions_user_project
            ON project_sessions(user_id, project_path)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
            ON audit_log(created_at)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_log_user_event
            ON audit_log(user_id, event_type)
            """
        )

    async def _migration_v2(self) -> None:
        conn = self._require_conn()
        columns = await self._get_table_columns("project_sessions")
        if "last_status" not in columns:
            await conn.execute(
                "ALTER TABLE project_sessions ADD COLUMN last_status TEXT NOT NULL DEFAULT ''"
            )
        if "last_error" not in columns:
            await conn.execute(
                "ALTER TABLE project_sessions ADD COLUMN last_error TEXT NOT NULL DEFAULT ''"
            )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_sessions_updated_at
            ON project_sessions(updated_at)
            """
        )

    async def _migration_v3(self) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_preferences (
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                launch_mode TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, project_path)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_preferences_updated_at
            ON project_preferences(updated_at)
            """
        )

    async def _migration_v4(self) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_session_resets (
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                reset_at_unix REAL NOT NULL,
                PRIMARY KEY (user_id, project_path)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_session_resets_reset_at
            ON project_session_resets(reset_at_unix)
            """
        )

    async def _migration_v5(self) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                current_project_path TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_preferences_updated_at
            ON user_preferences(updated_at)
            """
        )

    async def _migration_v6(self) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                last_update_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                first_prompt_preview TEXT NOT NULL DEFAULT '',
                last_progress_summary TEXT NOT NULL DEFAULT '',
                first_tool_name TEXT NOT NULL DEFAULT '',
                tool_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                stop_requested INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_runs_user_status
            ON project_runs(user_id, status, last_update_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_project_runs_user_project
            ON project_runs(user_id, project_path, last_update_at DESC)
            """
        )

    async def _migration_v7(self) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_recent_projects (
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, project_path)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_recent_projects_user_updated
            ON user_recent_projects(user_id, updated_at DESC)
            """
        )
        await conn.execute(
            """
            INSERT INTO user_recent_projects (user_id, project_path, updated_at)
            SELECT user_id, current_project_path, updated_at
            FROM user_preferences
            WHERE TRIM(current_project_path) != ''
            ON CONFLICT(user_id, project_path)
            DO UPDATE SET updated_at=excluded.updated_at
            """
        )

    async def _get_table_columns(self, table_name: str) -> set[str]:
        conn = self._require_conn()
        cursor = await conn.execute("PRAGMA table_info(%s)" % table_name)
        rows = await cursor.fetchall()
        return {str(row["name"]) for row in rows}

    async def upsert_session(
        self,
        user_id: int,
        project_path: str,
        thread_id: str,
        *,
        last_status: str = "",
        last_error: str = "",
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO project_sessions (
                user_id, project_path, thread_id, updated_at, last_status, last_error
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT(user_id, project_path)
            DO UPDATE SET
                thread_id=excluded.thread_id,
                updated_at=CURRENT_TIMESTAMP,
                last_status=excluded.last_status,
                last_error=excluded.last_error
            """,
            (user_id, project_path, thread_id, last_status, last_error),
        )
        await conn.execute(
            "DELETE FROM project_session_resets WHERE user_id = ? AND project_path = ?",
            (user_id, project_path),
        )
        await conn.commit()

    async def get_thread_id(self, user_id: int, project_path: str) -> Optional[str]:
        session = await self.get_session(user_id, project_path)
        return session.thread_id if session else None

    async def get_session(self, user_id: int, project_path: str) -> Optional[ProjectSession]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT user_id, project_path, thread_id, updated_at, last_status, last_error
            FROM project_sessions
            WHERE user_id = ? AND project_path = ?
            """,
            (user_id, project_path),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ProjectSession(
            user_id=int(row["user_id"]),
            project_path=str(row["project_path"]),
            thread_id=str(row["thread_id"]),
            updated_at=str(row["updated_at"]),
            last_status=str(row["last_status"] or ""),
            last_error=str(row["last_error"] or ""),
        )

    async def update_session_result(
        self,
        user_id: int,
        project_path: str,
        *,
        last_status: str,
        last_error: str = "",
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE project_sessions
            SET updated_at = CURRENT_TIMESTAMP, last_status = ?, last_error = ?
            WHERE user_id = ? AND project_path = ?
            """,
            (last_status, last_error, user_id, project_path),
        )
        await conn.commit()

    async def clear_session(self, user_id: int, project_path: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "DELETE FROM project_sessions WHERE user_id = ? AND project_path = ?",
            (user_id, project_path),
        )
        await conn.execute(
            """
            INSERT INTO project_session_resets (user_id, project_path, reset_at_unix)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, project_path)
            DO UPDATE SET reset_at_unix=excluded.reset_at_unix
            """,
            (user_id, project_path, time.time()),
        )
        await conn.commit()

    async def get_session_reset_at_unix(self, user_id: int, project_path: str) -> Optional[float]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT reset_at_unix
            FROM project_session_resets
            WHERE user_id = ? AND project_path = ?
            """,
            (user_id, project_path),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return float(row["reset_at_unix"])

    async def set_project_launch_mode(
        self,
        user_id: int,
        project_path: str,
        launch_mode: CodexLaunchMode,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO project_preferences (
                user_id, project_path, launch_mode, updated_at
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, project_path)
            DO UPDATE SET
                launch_mode=excluded.launch_mode,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, project_path, launch_mode.value),
        )
        await conn.commit()

    async def get_project_launch_mode(
        self,
        user_id: int,
        project_path: str,
    ) -> Optional[CodexLaunchMode]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT launch_mode
            FROM project_preferences
            WHERE user_id = ? AND project_path = ?
            """,
            (user_id, project_path),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return CodexLaunchMode.from_value(row["launch_mode"])

    async def set_current_project(self, user_id: int, project_path: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO user_preferences (user_id, current_project_path, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id)
            DO UPDATE SET
                current_project_path=excluded.current_project_path,
                updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, project_path),
        )
        await conn.execute(
            """
            INSERT INTO user_recent_projects (user_id, project_path, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, project_path)
            DO UPDATE SET updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, project_path),
        )
        await conn.commit()

    async def get_current_project(self, user_id: int) -> Optional[str]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT current_project_path
            FROM user_preferences
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        value = str(row["current_project_path"] or "").strip()
        return value or None

    async def finalize_orphaned_runs(
        self,
        *,
        error_message: str = "Bot restarted before run finished.",
    ) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(*)
            FROM project_runs
            WHERE status = ?
            """,
            (ProjectRunStatus.RUNNING.value,),
        )
        row = await cursor.fetchone()
        count = int(row[0] or 0) if row is not None else 0
        if count <= 0:
            return 0

        await conn.execute(
            """
            UPDATE project_runs
            SET status = ?,
                finished_at = CURRENT_TIMESTAMP,
                last_update_at = CURRENT_TIMESTAMP,
                error_message = CASE
                    WHEN TRIM(error_message) = '' THEN ?
                    ELSE error_message
                END
            WHERE status = ?
            """,
            (
                ProjectRunStatus.INTERRUPTED.value,
                error_message,
                ProjectRunStatus.RUNNING.value,
            ),
        )
        await conn.commit()
        return count

    async def list_recent_projects(
        self,
        user_id: int,
        *,
        available_project_paths: list[str],
        current_project_path: str = "",
        limit: int = 3,
    ) -> list[str]:
        if limit <= 0:
            return []
        conn = self._require_conn()
        available = {path for path in available_project_paths if path}
        if not available:
            return []

        cursor = await conn.execute(
            """
            SELECT project_path
            FROM user_recent_projects
            WHERE user_id = ?
            ORDER BY updated_at DESC, project_path ASC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()

        stale_paths: list[str] = []
        recent_paths: list[str] = []
        for row in rows:
            project_path = str(row["project_path"] or "").strip()
            if not project_path:
                continue
            if project_path not in available:
                stale_paths.append(project_path)
                continue
            if project_path not in recent_paths:
                recent_paths.append(project_path)

        if stale_paths:
            for project_path in stale_paths:
                await conn.execute(
                    "DELETE FROM user_recent_projects WHERE user_id = ? AND project_path = ?",
                    (user_id, project_path),
                )
            await conn.commit()

        if current_project_path and current_project_path in available:
            recent_paths = [path for path in recent_paths if path != current_project_path]
            recent_paths.insert(0, current_project_path)

        return recent_paths[:limit]

    async def log_audit_event(
        self,
        *,
        user_id: Optional[int],
        chat_id: Optional[int],
        project_path: Optional[str],
        event_type: str,
        event_status: str = "",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO audit_log(
                user_id, chat_id, project_path, event_type, event_status, details
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                chat_id,
                project_path,
                event_type,
                event_status,
                json.dumps(details or {}, ensure_ascii=True, sort_keys=True),
            ),
        )
        await conn.commit()

    async def create_project_run(
        self,
        *,
        user_id: int,
        project_path: str,
        thread_id: str = "",
        first_prompt_preview: str = "",
    ) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            INSERT INTO project_runs (
                user_id,
                project_path,
                thread_id,
                status,
                started_at,
                last_update_at,
                first_prompt_preview
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            """,
            (
                user_id,
                project_path,
                thread_id,
                ProjectRunStatus.RUNNING.value,
                first_prompt_preview,
            ),
        )
        await conn.commit()
        return int(cursor.lastrowid)

    async def update_project_run(
        self,
        run_id: int,
        *,
        thread_id: Optional[str] = None,
        status: Optional[ProjectRunStatus | str] = None,
        last_progress_summary: Optional[str] = None,
        first_tool_name: Optional[str] = None,
        tool_count: Optional[int] = None,
        error_message: Optional[str] = None,
        stop_requested: Optional[bool] = None,
        finished: bool = False,
    ) -> None:
        conn = self._require_conn()
        assignments = ["last_update_at = CURRENT_TIMESTAMP"]
        values: list[Any] = []
        if thread_id is not None:
            assignments.append("thread_id = ?")
            values.append(thread_id)
        if status is not None:
            assignments.append("status = ?")
            values.append(ProjectRunStatus.from_value(status).value)
        if last_progress_summary is not None:
            assignments.append("last_progress_summary = ?")
            values.append(last_progress_summary)
        if first_tool_name is not None:
            assignments.append("first_tool_name = ?")
            values.append(first_tool_name)
        if tool_count is not None:
            assignments.append("tool_count = ?")
            values.append(int(tool_count))
        if error_message is not None:
            assignments.append("error_message = ?")
            values.append(error_message)
        if stop_requested is not None:
            assignments.append("stop_requested = ?")
            values.append(1 if stop_requested else 0)
        if finished:
            assignments.append("finished_at = CURRENT_TIMESTAMP")
        values.append(run_id)
        await conn.execute(
            f"UPDATE project_runs SET {', '.join(assignments)} WHERE run_id = ?",
            tuple(values),
        )
        await conn.commit()

    async def get_project_run(self, run_id: int, *, user_id: Optional[int] = None) -> Optional[ProjectRun]:
        conn = self._require_conn()
        query = """
            SELECT run_id, user_id, project_path, thread_id, status, started_at, finished_at,
                   last_update_at, first_prompt_preview, last_progress_summary,
                   first_tool_name, tool_count, error_message, stop_requested
            FROM project_runs
            WHERE run_id = ?
        """
        params: list[Any] = [run_id]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        cursor = await conn.execute(query, tuple(params))
        row = await cursor.fetchone()
        return self._row_to_project_run(row) if row else None

    async def list_project_runs(
        self,
        *,
        user_id: int,
        project_path: Optional[str] = None,
        limit: int = 20,
        active_only: bool = False,
    ) -> list[ProjectRun]:
        conn = self._require_conn()
        query = """
            SELECT run_id, user_id, project_path, thread_id, status, started_at, finished_at,
                   last_update_at, first_prompt_preview, last_progress_summary,
                   first_tool_name, tool_count, error_message, stop_requested
            FROM project_runs
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if project_path is not None:
            query += " AND project_path = ?"
            params.append(project_path)
        if active_only:
            query += " AND status = ?"
            params.append(ProjectRunStatus.RUNNING.value)
        query += " ORDER BY last_update_at DESC, run_id DESC LIMIT ?"
        params.append(limit)
        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_project_run(row) for row in rows]

    async def list_project_activity_summaries(
        self,
        *,
        user_id: int,
        project_paths: list[str],
        current_project_path: str = "",
        limit_per_project: int = 5,
    ) -> list[ProjectActivitySummary]:
        if not project_paths:
            return []
        runs = await self.list_project_runs(user_id=user_id, limit=max(len(project_paths) * limit_per_project, 1))
        session_map: dict[str, ProjectSession] = {}
        for project_path in project_paths:
            session = await self.get_session(user_id, project_path)
            if session is not None:
                session_map[project_path] = session

        runs_by_project: dict[str, list[ProjectRun]] = {project_path: [] for project_path in project_paths}
        for run in runs:
            if run.project_path in runs_by_project and len(runs_by_project[run.project_path]) < limit_per_project:
                runs_by_project[run.project_path].append(run)

        summaries: list[ProjectActivitySummary] = []
        for project_path in project_paths:
            project_runs = runs_by_project.get(project_path, [])
            active_run = next((run for run in project_runs if run.is_active), None)
            latest_run = project_runs[0] if project_runs else None
            session = session_map.get(project_path)
            summaries.append(
                ProjectActivitySummary(
                    project_path=project_path,
                    project_name=Path(project_path).name,
                    is_current=project_path == current_project_path,
                    current_session_thread_id=session.thread_id if session else "",
                    active_run=active_run,
                    latest_run=latest_run,
                    recent_run_count=len(project_runs),
                )
            )
        summaries.sort(
            key=lambda item: (
                0 if item.active_run else 1,
                -(item.active_run or item.latest_run).last_update_at.timestamp()
                if (item.active_run or item.latest_run)
                else 0.0,
                item.project_name,
            )
        )
        return summaries

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @classmethod
    def _row_to_project_run(cls, row: sqlite3.Row) -> ProjectRun:
        return ProjectRun(
            run_id=int(row["run_id"]),
            user_id=int(row["user_id"]),
            project_path=str(row["project_path"]),
            thread_id=str(row["thread_id"] or ""),
            status=ProjectRunStatus.from_value(row["status"]),
            started_at=cls._parse_timestamp(row["started_at"]),
            finished_at=cls._parse_timestamp(row["finished_at"]) if row["finished_at"] else None,
            last_update_at=cls._parse_timestamp(row["last_update_at"]),
            first_prompt_preview=str(row["first_prompt_preview"] or ""),
            last_progress_summary=str(row["last_progress_summary"] or ""),
            first_tool_name=str(row["first_tool_name"] or ""),
            tool_count=int(row["tool_count"] or 0),
            error_message=str(row["error_message"] or ""),
            stop_requested=bool(row["stop_requested"]),
        )

    async def health_check(self) -> bool:
        conn = self._require_conn()
        try:
            cursor = await conn.execute("SELECT 1")
            row = await cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception:
            return False

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    def _require_conn(self) -> _AsyncConnection:
        if self.conn is None:
            raise RuntimeError("SessionStore is not initialized")
        return self.conn
