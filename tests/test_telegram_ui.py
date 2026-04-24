from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from codex_telegram_bot.models import (
    CodexLaunchMode,
    CodexResponse,
    CodexResultStatus,
    LocalCodexSession,
    ProjectActivitySummary,
    ProjectRun,
    ProjectRunStatus,
)
from codex_telegram_bot.services.projects import RecentProjectOption, RepoOption
from codex_telegram_bot.telegram.ui.keyboards import (
    build_local_sessions_keyboard,
    build_mode_editor_keyboard,
    build_project_runs_keyboard,
    build_repo_keyboard,
    build_run_detail_keyboard,
    build_session_keyboard,
    build_workspace_keyboard,
)
from codex_telegram_bot.telegram.ui.responder import TelegramResponder
from codex_telegram_bot.telegram.ui.texts import (
    render_final_text,
    render_full_access_warning_text,
    render_launch_mode_editor_text,
    render_project_runs_text,
    render_repo_picker_text,
    render_run_detail_text,
    render_session_text,
    render_workspace_text,
)


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def debug(self, event: str, **kwargs) -> None:
        self.events.append(("debug", event, kwargs))

    def warning(self, event: str, **kwargs) -> None:
        self.events.append(("warning", event, kwargs))


def keyboard_callback_data(markup) -> list[list[str]]:
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def test_build_session_keyboard_has_expected_actions() -> None:
    markup = build_session_keyboard()

    assert keyboard_callback_data(markup) == [
        ["nav:repo", "session:list"],
        ["workspace:list", "mode:show"],
        ["action:new"],
    ]


def test_build_session_keyboard_adds_recent_project_shortcuts() -> None:
    markup = build_session_keyboard(
        [
            RecentProjectOption(slug="api", label="api", is_current=True),
            RecentProjectOption(slug="web", label="web"),
            RecentProjectOption(slug="ops", label="ops"),
        ]
    )

    assert keyboard_callback_data(markup) == [
        ["nav:repo", "session:list"],
        ["workspace:list", "mode:show"],
        ["action:new"],
        ["repo:quick:api", "repo:quick:web", "repo:quick:ops", "nav:repo"],
    ]
    assert markup.inline_keyboard[3][0].text == "◉ api"
    assert markup.inline_keyboard[3][3].text == "Ещё…"


def test_build_local_sessions_keyboard_uses_prompt_and_short_id_fallback() -> None:
    updated_at = datetime(2026, 4, 22, 18, 30)
    sessions = [
        LocalCodexSession(
            session_id="session-with-prompt",
            cwd=Path("/tmp/app"),
            created_at=updated_at,
            updated_at=updated_at,
            source_path=Path("/tmp/session-with-prompt.jsonl"),
            first_prompt="Fix the Telegram session picker routing",
        ),
        LocalCodexSession(
            session_id="fallback-session",
            cwd=Path("/tmp/app"),
            created_at=updated_at,
            updated_at=updated_at,
            source_path=Path("/tmp/fallback-session.jsonl"),
            first_prompt="",
        ),
    ]

    markup = build_local_sessions_keyboard(sessions)

    assert keyboard_callback_data(markup) == [
        ["session:select:session-with-prompt"],
        ["session:select:fallback-session"],
        ["session:refresh", "action:new"],
        ["nav:menu"],
    ]
    assert markup.inline_keyboard[0][0].text == (
        "2026-04-22 18:30 · Fix the Telegram session picker routing"
    )
    assert markup.inline_keyboard[1][0].text == "2026-04-22 18:30 · fallback"


def test_build_repo_keyboard_ends_with_back_to_menu() -> None:
    markup = build_repo_keyboard([RepoOption(slug="api", label="api")])

    assert keyboard_callback_data(markup) == [
        ["repo:select:api"],
        ["action:create_project"],
        ["nav:menu"],
    ]


def test_render_repo_picker_text_marks_current_project() -> None:
    text = render_repo_picker_text(
        [RepoOption(slug="api", label="api", is_current=True)],
        truncated=False,
        auto_created=True,
    )

    assert "Создан первый проект `api`." in text
    assert "Текущий: `api`" in text


def test_render_final_text_appends_interrupted_marker() -> None:
    text = render_final_text(
        CodexResponse(
            final_text="Stopped",
            thread_id="thread-1",
            status=CodexResultStatus.INTERRUPTED,
        )
    )

    assert "(Interrupted by user)" in text


def test_render_session_text_mentions_recent_projects_shortcut() -> None:
    text = render_session_text(
        cwd=Path("/tmp/api"),
        launch_mode=CodexLaunchMode.SANDBOX,
        has_session=True,
        has_active_run=False,
        recent_project_count=2,
    )

    assert "Недавние: переключение в один тап." in text


def test_build_mode_editor_keyboard_marks_selected_mode() -> None:
    markup = build_mode_editor_keyboard(
        CodexLaunchMode.FULL_ACCESS,
        full_access_confirmed=False,
        back_callback="nav:menu",
    )

    assert keyboard_callback_data(markup) == [
        ["mode:set:sandbox", "mode:confirm_full"],
        ["nav:menu"],
    ]
    assert markup.inline_keyboard[0][1].text == "Полный доступ (подтвердить)"


def test_render_mode_editor_text_mentions_next_requests() -> None:
    text = render_launch_mode_editor_text(
        project_name="api",
        launch_mode=CodexLaunchMode.FULL_ACCESS,
        has_active_run=True,
    )

    assert "Проект: `api`" in text
    assert "Полный доступ" in text
    assert "следующему запросу" in text
    assert "Текущий запуск не изменится." in text


def test_render_full_access_warning_mentions_project() -> None:
    text = render_full_access_warning_text(project_name="api")

    assert "Подтверждение полного доступа" in text
    assert "Проект: `api`" in text


def make_project_run(*, run_id: int = 7, project_path: str = "/tmp/api", status=ProjectRunStatus.RUNNING) -> ProjectRun:
    timestamp = datetime(2026, 4, 23, 12, 0)
    return ProjectRun(
        run_id=run_id,
        user_id=42,
        project_path=project_path,
        thread_id="thread-123",
        status=status,
        started_at=timestamp,
        finished_at=None if status == ProjectRunStatus.RUNNING else timestamp,
        last_update_at=timestamp,
        first_prompt_preview="Fix routing",
        last_progress_summary="🔧 Read",
        first_tool_name="Read",
        tool_count=1,
    )


def test_build_workspace_keyboard_prefers_active_run() -> None:
    summary = ProjectActivitySummary(
        project_path="/tmp/api",
        project_name="api",
        is_current=True,
        active_run=make_project_run(run_id=7),
        recent_run_count=1,
    )
    markup = build_workspace_keyboard([summary])
    assert keyboard_callback_data(markup) == [
        ["run:attach:7", "run:view:7"],
        ["workspace:list"],
        ["nav:menu"],
    ]


def test_render_workspace_text_includes_project_and_progress() -> None:
    summary = ProjectActivitySummary(
        project_path="/tmp/api",
        project_name="api",
        is_current=True,
        current_session_thread_id="thread-123",
        active_run=make_project_run(run_id=7),
        recent_run_count=1,
    )
    text = render_workspace_text([summary], active_run_count=1, active_run_limit=5)
    assert "`api`" in text
    assert "Активных процессов: `1`" in text
    assert "🔧 Read" in text
    assert "сессия `thread-1`" in text


def test_project_run_and_detail_keyboards() -> None:
    run = make_project_run(run_id=9)
    runs_markup = build_project_runs_keyboard("api", [run])
    detail_markup = build_run_detail_keyboard(run, user_id=42, attach_enabled=True)
    assert keyboard_callback_data(runs_markup) == [
        ["run:view:9"],
        ["run:list:api", "workspace:list"],
        ["nav:menu"],
    ]
    assert keyboard_callback_data(detail_markup) == [
        ["run:attach:9"],
        ["action:stop:9:42"],
        ["repo:select:api"],
        ["run:list:api"],
        ["workspace:list"],
    ]


def test_render_project_runs_and_run_detail_text() -> None:
    run = make_project_run(run_id=9)
    list_text = render_project_runs_text(project_name="api", runs=[run], current_thread_id="thread-123")
    detail_text = render_run_detail_text(run, current_session_thread_id="thread-123", is_current_project=True)
    assert "#9" in list_text
    assert "Карточка процесса." in detail_text
    assert "Первый инструмент: `Read`" in detail_text


@pytest.mark.asyncio
async def test_responder_logs_noop_callback_edit() -> None:
    logger = FakeLogger()
    responder = TelegramResponder(logger)

    class FakeQuery:
        async def edit_message_text(self, *args, **kwargs) -> None:
            raise RuntimeError("Message is not modified: same content")

    update = type("Update", (), {"callback_query": FakeQuery(), "effective_message": None})()
    await responder.edit_callback_message(update, "same")

    assert logger.events[0][1] == "telegram_callback_edit_noop"
