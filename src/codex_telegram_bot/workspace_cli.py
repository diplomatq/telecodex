from __future__ import annotations

import argparse
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .models import ProjectRun
from .services.projects import ProjectService
from .telegram.ui.texts import render_project_display_name, render_run_status_label


def _format_duration(run: ProjectRun) -> str:
    started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
    end = run.finished_at or run.last_update_at
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    seconds = max(int((end - started).total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _run_sync(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-telegram-workspace")
    subparsers = parser.add_subparsers(dest="entity", required=True)

    workspace_parser = subparsers.add_parser("workspace")
    workspace_parser.add_argument("--project", default="")

    run_parser = subparsers.add_parser("run")
    run_subparsers = run_parser.add_subparsers(dest="action", required=True)
    run_show = run_subparsers.add_parser("show")
    run_show.add_argument("run_id", type=int)
    run_attach = run_subparsers.add_parser("attach")
    run_attach.add_argument("run_id", type=int)
    run_stop = run_subparsers.add_parser("stop")
    run_stop.add_argument("run_id", type=int)

    project_parser = subparsers.add_parser("project")
    project_subparsers = project_parser.add_subparsers(dest="action", required=True)
    project_switch = project_subparsers.add_parser("switch")
    project_switch.add_argument("slug")

    args = parser.parse_args(argv)
    settings = Settings()
    project_service = ProjectService(settings, _noop_record_event)
    _ensure_schema(settings.sqlite_path)
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        user_id = settings.allowed_users[0] if settings.allowed_users else 0
        if args.entity == "workspace":
            if args.project:
                project_path = str(project_service.resolve_repo_slug(args.project, user_id=user_id).resolve())
                rows = conn.execute(
                    """
                    SELECT run_id, project_path, thread_id, status, started_at, finished_at, last_update_at
                    FROM project_runs
                    WHERE user_id = ? AND project_path = ?
                    ORDER BY last_update_at DESC, run_id DESC
                    LIMIT 10
                    """,
                    (user_id, project_path),
                ).fetchall()
                print(render_project_display_name(Path(project_path)))
                for row in rows:
                    run = _row_to_run(row)
                    print(
                        f"#{run.run_id} {render_run_status_label(run.status)} {_format_duration(run)} {run.thread_id[:8] or 'none'}"
                    )
                return 0
            current_row = conn.execute(
                "SELECT current_project_path FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current_project = str(current_row["current_project_path"]) if current_row else ""
            for path in project_service.list_project_paths(user_id=user_id):
                row = conn.execute(
                    """
                    SELECT run_id, project_path, thread_id, status, started_at, finished_at, last_update_at
                    FROM project_runs
                    WHERE user_id = ? AND project_path = ?
                    ORDER BY last_update_at DESC, run_id DESC
                    LIMIT 1
                    """,
                    (user_id, str(path.resolve())),
                ).fetchone()
                run = _row_to_run(row) if row else None
                if run is None:
                    continue
                status = render_run_status_label(run.status)
                duration = _format_duration(run)
                marker = "*" if str(path.resolve()) == current_project else "-"
                print(f"{marker} {project_service.render_project_label(path)}: {status} {duration}")
            return 0
        if args.entity == "run":
            row = conn.execute(
                """
                SELECT run_id, project_path, thread_id, status, started_at, finished_at, last_update_at,
                       last_progress_summary
                FROM project_runs
                WHERE run_id = ? AND user_id = ?
                """,
                (args.run_id, user_id),
            ).fetchone()
            if row is None:
                print("Run not found")
                return 1
            run = _row_to_run(row)
            if args.action == "show":
                print(f"run_id={run.run_id}")
                print(f"project={render_project_display_name(Path(run.project_path))}")
                print(f"status={render_run_status_label(run.status)}")
                print(f"thread_id={run.thread_id or 'none'}")
                print(f"duration={_format_duration(run)}")
                progress = str(row["last_progress_summary"] or "").strip()
                if progress:
                    print(f"progress={progress}")
                return 0
            if args.action == "attach":
                if not run.thread_id:
                    print("Run has no thread id")
                    return 1
                conn.execute(
                    """
                    INSERT INTO project_sessions (user_id, project_path, thread_id, title, updated_at, last_status, last_error)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, '')
                    ON CONFLICT(user_id, project_path)
                    DO UPDATE SET thread_id=excluded.thread_id, title=excluded.title, updated_at=CURRENT_TIMESTAMP, last_status=excluded.last_status
                    """,
                    (user_id, run.project_path, run.thread_id, "", "selected"),
                )
                conn.execute(
                    """
                    INSERT INTO user_preferences (user_id, current_project_path, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id)
                    DO UPDATE SET current_project_path=excluded.current_project_path, updated_at=CURRENT_TIMESTAMP
                    """,
                    (user_id, run.project_path),
                )
                conn.commit()
                print(f"Attached {run.thread_id[:8]} to {render_project_display_name(Path(run.project_path))}")
                return 0
            if args.action == "stop":
                if not run.is_active:
                    print("Run already finished")
                    return 1
                conn.execute(
                    "UPDATE project_runs SET stop_requested = 1, last_update_at = CURRENT_TIMESTAMP WHERE run_id = ?",
                    (run.run_id,),
                )
                conn.commit()
                print(f"Stop requested for run {run.run_id}")
                return 0
        if args.entity == "project" and args.action == "switch":
            try:
                project_path = project_service.resolve_repo_slug(args.slug, user_id=user_id)
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                print("Project not found")
                return 1
            conn.execute(
                """
                INSERT INTO user_preferences (user_id, current_project_path, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id)
                DO UPDATE SET current_project_path=excluded.current_project_path, updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, str(project_path)),
            )
            conn.commit()
            print(f"Current project: {render_project_display_name(project_path)}")
            return 0
        return 1
    finally:
        conn.close()


def _row_to_run(row: sqlite3.Row) -> ProjectRun:
    from .models import ProjectRunStatus

    def parse(value: object):
        text = str(value or "").strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    started_at = parse(row["started_at"])
    finished_at = parse(row["finished_at"])
    last_update_at = parse(row["last_update_at"])
    assert started_at is not None
    assert last_update_at is not None
    return ProjectRun(
        run_id=int(row["run_id"]),
        user_id=0,
        project_path=str(row["project_path"]),
        thread_id=str(row["thread_id"] or ""),
        status=ProjectRunStatus.from_value(row["status"]),
        started_at=started_at,
        finished_at=finished_at,
        last_update_at=last_update_at,
        last_progress_summary=str(row["last_progress_summary"] or "") if "last_progress_summary" in row.keys() else "",
    )


def _ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=db_path.parent, delete=True) as _:
        pass
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                current_project_path TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_sessions (
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_status TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (user_id, project_path)
            )
            """
        )
        conn.execute(
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
        conn.commit()
    finally:
        conn.close()


async def _noop_record_event(*args, **kwargs) -> None:
    return None


def run(argv: list[str] | None = None) -> int:
    return _run_sync(argv)
