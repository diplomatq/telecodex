from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codex_telegram_bot import workspace_cli
from codex_telegram_bot.config import Settings


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "telegram_bot_username": "codex_bot",
        "approved_directory": tmp_path,
        "allowed_users": "42",
        "database_url": f"sqlite:///{tmp_path / 'db.sqlite3'}",
    }
    values.update(overrides)
    values.setdefault("_env_file", None)
    return Settings(**values)


def test_workspace_cli_summary_and_attach(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "api"
    project_dir.mkdir()
    settings = make_settings(tmp_path)
    monkeypatch.setattr(workspace_cli, "Settings", lambda: settings)
    workspace_cli._ensure_schema(settings.sqlite_path)
    conn = sqlite3.connect(settings.sqlite_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO project_runs (
                user_id, project_path, thread_id, status, started_at, finished_at, last_update_at,
                first_prompt_preview, last_progress_summary, first_tool_name, tool_count, error_message, stop_requested
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            """,
            (42, str(project_dir.resolve()), "thread-123", "success", "Fix API", "Done", "Read", 1, "", 0),
        )
        run_id = int(cursor.lastrowid)
        conn.commit()
    finally:
        conn.close()

    assert workspace_cli.run(["workspace"]) == 0
    assert "api: success" in capsys.readouterr().out

    assert workspace_cli.run(["run", "attach", str(run_id)]) == 0
    output = capsys.readouterr().out
    assert f"Attached thread-1 to {str(project_dir.resolve().parent / project_dir.resolve().name)}" in output

    conn = sqlite3.connect(settings.sqlite_path)
    try:
        current_project = conn.execute(
            "SELECT current_project_path FROM user_preferences WHERE user_id = ?",
            (42,),
        ).fetchone()[0]
        thread_id = conn.execute(
            "SELECT thread_id FROM project_sessions WHERE user_id = ? AND project_path = ?",
            (42, str(project_dir.resolve())),
        ).fetchone()[0]
    finally:
        conn.close()
    assert current_project == str(project_dir.resolve())
    assert thread_id == "thread-123"


def test_workspace_cli_stop_and_project_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "api"
    other_dir = tmp_path / "web"
    project_dir.mkdir()
    other_dir.mkdir()
    settings = make_settings(tmp_path)
    monkeypatch.setattr(workspace_cli, "Settings", lambda: settings)
    workspace_cli._ensure_schema(settings.sqlite_path)

    conn = sqlite3.connect(settings.sqlite_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO project_runs (
                user_id, project_path, thread_id, status, started_at, last_update_at,
                first_prompt_preview, last_progress_summary, first_tool_name, tool_count, error_message, stop_requested
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            """,
            (42, str(project_dir.resolve()), "thread-run", "running", "Prompt", "Working", "Read", 1, "", 0),
        )
        run_id = int(cursor.lastrowid)
        conn.commit()
    finally:
        conn.close()

    assert workspace_cli.run(["run", "stop", str(run_id)]) == 0
    assert f"Stop requested for run {run_id}" in capsys.readouterr().out

    conn = sqlite3.connect(settings.sqlite_path)
    try:
        stop_requested = conn.execute(
            "SELECT stop_requested FROM project_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert stop_requested == 1

    assert workspace_cli.run(["project", "switch", "web"]) == 0
    assert "Current project: web" in capsys.readouterr().out


def test_workspace_cli_hides_idle_and_supports_extra_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "api"
    extra_root = tmp_path / "extra"
    extra_project = extra_root / "worker"
    project_dir.mkdir()
    extra_root.mkdir()
    extra_project.mkdir()
    settings = make_settings(
        tmp_path,
        additional_project_directories=[extra_root],
        project_ignore_names=["ignored"],
    )
    monkeypatch.setattr(workspace_cli, "Settings", lambda: settings)
    workspace_cli._ensure_schema(settings.sqlite_path)

    conn = sqlite3.connect(settings.sqlite_path)
    try:
        conn.execute(
            """
            INSERT INTO project_runs (
                user_id, project_path, thread_id, status, started_at, finished_at, last_update_at,
                first_prompt_preview, last_progress_summary, first_tool_name, tool_count, error_message, stop_requested
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            """,
            (42, str(extra_project.resolve()), "thread-1", "success", "Prompt", "Done", "Read", 1, "", 0),
        )
        conn.commit()
    finally:
        conn.close()

    assert workspace_cli.run(["workspace"]) == 0
    output = capsys.readouterr().out
    assert "api: idle" not in output
    assert "extra/worker: success" in output
