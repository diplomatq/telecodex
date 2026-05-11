from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codex_telegram_bot.models import CodexLaunchMode
from codex_telegram_bot.models import ProjectRunStatus
from codex_telegram_bot.session_store import SessionStore


@pytest.mark.asyncio
async def test_session_store_crud_and_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()

    assert await store.health_check() is True

    await store.upsert_session(
        100,
        "/workspace/project",
        "thread-1",
        title="Fix Telegram callback routing",
        last_status="success",
        last_error="",
    )
    session = await store.get_session(100, "/workspace/project")
    assert session is not None
    assert session.thread_id == "thread-1"
    assert session.title == "Fix Telegram callback routing"
    assert session.last_status == "success"

    await store.update_session_result(
        100,
        "/workspace/project",
        last_status="timeout",
        last_error="timed out",
    )
    session = await store.get_session(100, "/workspace/project")
    assert session is not None
    assert session.last_status == "timeout"
    assert session.last_error == "timed out"

    assert await store.get_project_launch_mode(100, "/workspace/project") is None
    await store.set_project_launch_mode(100, "/workspace/project", CodexLaunchMode.FULL_ACCESS)
    assert await store.get_project_launch_mode(100, "/workspace/project") == CodexLaunchMode.FULL_ACCESS
    assert await store.get_current_project(100) is None
    await store.set_current_project(100, "/workspace/project")
    assert await store.get_current_project(100) == "/workspace/project"
    assert await store.list_recent_projects(
        100,
        available_project_paths=["/workspace/project"],
        current_project_path="/workspace/project",
    ) == ["/workspace/project"]

    await store.log_audit_event(
        user_id=100,
        chat_id=200,
        project_path="/workspace/project",
        event_type="request_finished",
        event_status="timeout",
        details={"duration_ms": 1234},
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT event_type, event_status, details FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "request_finished"
    assert row[1] == "timeout"
    assert '"duration_ms": 1234' in row[2]

    await store.clear_session(100, "/workspace/project")
    assert await store.get_session(100, "/workspace/project") is None
    assert await store.get_session_reset_at_unix(100, "/workspace/project") is not None

    await store.upsert_session(100, "/workspace/project", "thread-2", title="Fix Telegram callback routing", last_status="success")
    assert await store.get_session_reset_at_unix(100, "/workspace/project") is None
    await store.close()


@pytest.mark.asyncio
async def test_session_store_project_runs_and_workspace_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()

    await store.upsert_session(100, "/workspace/api", "thread-api", title="Fix API telemetry", last_status="success")
    run1 = await store.create_project_run(
        user_id=100,
        project_path="/workspace/api",
        thread_id="thread-api",
        first_prompt_preview="Fix API telemetry",
    )
    await store.update_project_run(
        run1,
        last_progress_summary="🔧 Read",
        first_tool_name="Read",
        tool_count=1,
    )
    run2 = await store.create_project_run(
        user_id=100,
        project_path="/workspace/web",
        first_prompt_preview="Update dashboard",
    )
    await store.update_project_run(
        run2,
        status="success",
        thread_id="thread-web",
        last_progress_summary="💬 Done",
        finished=True,
    )

    fetched = await store.get_project_run(run1, user_id=100)
    assert fetched is not None
    assert fetched.thread_id == "thread-api"
    assert fetched.first_tool_name == "Read"
    assert fetched.tool_count == 1

    api_runs = await store.list_project_runs(user_id=100, project_path="/workspace/api")
    assert [run.run_id for run in api_runs] == [run1]

    summaries = await store.list_project_activity_summaries(
        user_id=100,
        project_paths=["/workspace/api", "/workspace/web"],
        current_project_path="/workspace/api",
    )
    assert summaries[0].project_name == "workspace/api"
    assert summaries[0].is_current is True
    assert summaries[0].active_run is not None
    assert summaries[0].current_session_thread_id == "thread-api"
    assert summaries[0].current_session_title == "Fix API telemetry"
    web_summary = next(summary for summary in summaries if summary.project_name == "workspace/web")
    assert web_summary.latest_run is not None
    assert web_summary.latest_run.status.value == "success"
    await store.close()


@pytest.mark.asyncio
async def test_session_store_recent_projects_prioritizes_current_and_removes_stale(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()

    await store.set_current_project(100, "/workspace/api")
    await store.set_current_project(100, "/workspace/web")
    await store.set_current_project(100, "/workspace/ops")

    recent = await store.list_recent_projects(
        100,
        available_project_paths=["/workspace/api", "/workspace/web", "/workspace/ops"],
        current_project_path="/workspace/api",
    )
    assert recent == ["/workspace/api", "/workspace/ops", "/workspace/web"]

    filtered = await store.list_recent_projects(
        100,
        available_project_paths=["/workspace/api", "/workspace/web"],
        current_project_path="/workspace/api",
    )
    assert filtered == ["/workspace/api", "/workspace/web"]

    rows = await store.conn.execute_fetchall(
        "SELECT project_path FROM user_recent_projects WHERE user_id = 100 ORDER BY project_path"
    )
    assert [row["project_path"] for row in rows] == ["/workspace/api", "/workspace/web"]
    await store.close()


@pytest.mark.asyncio
async def test_session_store_hidden_projects_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()

    assert await store.is_project_hidden(100, "/workspace/api") is False
    await store.set_project_hidden_state(100, "/workspace/api", hidden=True)
    await store.set_project_hidden_state(100, "/workspace/web", hidden=True)

    assert await store.is_project_hidden(100, "/workspace/api") is True
    assert await store.list_hidden_projects(100) == ["/workspace/web", "/workspace/api"] or await store.list_hidden_projects(100) == ["/workspace/api", "/workspace/web"]

    await store.set_project_hidden_state(100, "/workspace/api", hidden=False)

    assert await store.is_project_hidden(100, "/workspace/api") is False
    assert await store.list_hidden_projects(100) == ["/workspace/web"]
    await store.close()


@pytest.mark.asyncio
async def test_session_store_migrates_hidden_projects_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_hidden.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (8)")
        conn.commit()
    finally:
        conn.close()

    store = SessionStore(db_path)
    await store.initialize()
    await store.set_project_hidden_state(1, "/legacy/project", hidden=True)

    assert await store.is_project_hidden(1, "/legacy/project") is True
    await store.close()


@pytest.mark.asyncio
async def test_session_store_migrates_session_title_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_titles.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (9)")
        conn.execute(
            """
            CREATE TABLE project_sessions (
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
        conn.execute(
            """
            CREATE TABLE project_session_resets (
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                reset_at_unix REAL NOT NULL,
                PRIMARY KEY (user_id, project_path)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = SessionStore(db_path)
    await store.initialize()
    await store.upsert_session(1, "/legacy/project", "thread-1", title="Fix callback flow", last_status="success")

    session = await store.get_session(1, "/legacy/project")
    assert session is not None
    assert session.title == "Fix callback flow"
    await store.close()


@pytest.mark.asyncio
async def test_session_store_finalize_orphaned_runs_marks_running_as_orphaned_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "bot.db"
    store = SessionStore(db_path)
    await store.initialize()

    running_id = await store.create_project_run(
        user_id=100,
        project_path="/workspace/api",
        thread_id="thread-api",
        first_prompt_preview="Keep going",
    )
    done_id = await store.create_project_run(
        user_id=100,
        project_path="/workspace/web",
        thread_id="thread-web",
        first_prompt_preview="Done already",
    )
    await store.update_project_run(done_id, status=ProjectRunStatus.SUCCESS, finished=True)

    finalized = await store.finalize_orphaned_runs()

    assert finalized == 1
    running = await store.get_project_run(running_id, user_id=100)
    done = await store.get_project_run(done_id, user_id=100)
    assert running is not None
    assert running.status == ProjectRunStatus.ORPHANED_AFTER_RESTART
    assert running.finished_at is not None
    assert running.error_message == "Bot restarted before run finished."
    assert done is not None
    assert done.status == ProjectRunStatus.SUCCESS
    await store.close()


@pytest.mark.asyncio
async def test_session_store_migrates_legacy_interrupted_runs_to_stopped_by_user(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_runs.db"
    store = SessionStore(db_path)
    await store.initialize()

    run_id = await store.create_project_run(
        user_id=100,
        project_path="/workspace/api",
        thread_id="thread-api",
        first_prompt_preview="Stop me",
    )
    await store.update_project_run(run_id, status="interrupted", finished=True)
    await store.close()

    migrated_store = SessionStore(db_path)
    await migrated_store.initialize()

    run = await migrated_store.get_project_run(run_id, user_id=100)

    assert run is not None
    assert run.status == ProjectRunStatus.STOPPED_BY_USER
    await migrated_store.close()


@pytest.mark.asyncio
async def test_session_store_migrates_legacy_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE project_sessions (
                user_id INTEGER NOT NULL,
                project_path TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, project_path)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = SessionStore(db_path)
    await store.initialize()
    await store.upsert_session(1, "/legacy/project", "thread-legacy", last_status="success")

    migrated = await store.get_session(1, "/legacy/project")
    assert migrated is not None
    assert migrated.last_status == "success"
    assert migrated.last_error == ""
    await store.set_project_launch_mode(1, "/legacy/project", CodexLaunchMode.SANDBOX)
    assert await store.get_project_launch_mode(1, "/legacy/project") == CodexLaunchMode.SANDBOX
    await store.set_current_project(1, "/legacy/project")
    assert await store.get_current_project(1) == "/legacy/project"
    await store.close()
