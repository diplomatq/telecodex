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
from codex_telegram_bot.services.projects import ProjectVisibilityOption, RecentProjectOption, RepoOption
from codex_telegram_bot.telegram.ui.keyboards import (
    build_local_sessions_keyboard,
    build_mode_editor_keyboard,
    build_project_visibility_keyboard,
    build_project_runs_keyboard,
    build_repo_keyboard,
    build_run_detail_keyboard,
    build_settings_keyboard,
    build_session_keyboard,
    build_workspace_keyboard,
)
from codex_telegram_bot.telegram.ui.responder import TelegramResponder
from codex_telegram_bot.telegram.ui.texts import (
    render_final_text,
    render_full_access_warning_text,
    render_launch_mode_editor_text,
    render_project_visibility_text,
    render_project_runs_text,
    render_repo_picker_text,
    render_run_detail_text,
    render_session_text,
    render_settings_text,
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
        ["settings:show"],
        ["action:new"],
    ]


def test_build_session_keyboard_adds_resume_action_when_session_exists() -> None:
    markup = build_session_keyboard(has_resume_session=True)

    assert keyboard_callback_data(markup) == [
        ["nav:repo", "session:list"],
        ["workspace:list", "mode:show"],
        ["settings:show"],
        ["session:resume_current", "action:new"],
    ]


def test_build_session_keyboard_adds_recent_project_shortcuts() -> None:
    markup = build_session_keyboard(
        [
            RecentProjectOption(key="/tmp/api", slug="api", label="api", is_current=True),
            RecentProjectOption(key="/tmp/web", slug="web", label="web"),
            RecentProjectOption(key="/tmp/ops", slug="ops", label="ops"),
            RecentProjectOption(key="/tmp/ml", slug="ml", label="ml"),
            RecentProjectOption(key="/tmp/bot", slug="bot", label="bot"),
        ]
    )

    assert keyboard_callback_data(markup) == [
        ["nav:repo", "session:list"],
        ["workspace:list", "mode:show"],
        ["settings:show"],
        ["action:new"],
        ["repo:quick:/tmp/api", "repo:quick:/tmp/web", "repo:quick:/tmp/ops"],
        ["repo:quick:/tmp/ml", "repo:quick:/tmp/bot"],
        ["nav:repo"],
    ]
    assert markup.inline_keyboard[4][0].text == "◉ api"
    assert markup.inline_keyboard[6][0].text == "Ещё…"


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
    markup = build_repo_keyboard([RepoOption(key="/tmp/api", slug="api", label="api")])

    assert keyboard_callback_data(markup) == [
        ["repo:select:/tmp/api"],
        ["action:create_project"],
        ["nav:menu"],
    ]


def test_render_repo_picker_text_marks_current_project() -> None:
    text = render_repo_picker_text(
        [RepoOption(key="/tmp/api", slug="api", label="api", is_current=True)],
        truncated=False,
        auto_created=True,
    )

    assert "Создан первый проект `api`." in text
    assert "Текущий: `api`" in text


def test_render_repo_picker_text_uses_labels() -> None:
    text = render_repo_picker_text(
        [RepoOption(key="/tmp/extra/worker", slug="worker", label="extra/worker", is_current=True)],
        truncated=False,
        auto_created=False,
    )

    assert "Текущий: `extra/worker`" in text


def test_settings_keyboard_and_text() -> None:
    markup = build_settings_keyboard()
    text = render_settings_text(current_project=Path("/tmp/team/api"))

    assert keyboard_callback_data(markup) == [
        ["settings:projects"],
        ["nav:menu"],
    ]
    assert "Настройки." in text
    assert "Проект: `team/api`" in text


def test_project_visibility_keyboard_and_text() -> None:
    entries = [
        ProjectVisibilityOption(key="/tmp/team/api", label="team/api", is_hidden=False, is_current=True),
        ProjectVisibilityOption(key="/tmp/extra/worker", label="extra/worker", is_hidden=True, is_current=False),
    ]

    markup = build_project_visibility_keyboard(entries)
    text = render_project_visibility_text(entries)

    assert keyboard_callback_data(markup) == [
        ["project:hide:/tmp/team/api"],
        ["project:show:/tmp/extra/worker"],
        ["settings:projects:0"],
        ["settings:show"],
    ]
    assert "◉ `team/api`" in text
    assert "🙈 `extra/worker`" in text
    assert "Страница `1/1`" in text


def test_project_visibility_keyboard_supports_pagination() -> None:
    entries = [
        ProjectVisibilityOption(key=f"/tmp/p{i}", label=f"group/project-{i}", is_hidden=False, is_current=False)
        for i in range(25)
    ]

    first_page_markup = build_project_visibility_keyboard(entries, page=0)
    second_page_markup = build_project_visibility_keyboard(entries, page=1)
    first_page_text = render_project_visibility_text(entries, page=0)
    second_page_text = render_project_visibility_text(entries, page=1)

    assert first_page_markup.inline_keyboard[20][0].callback_data == "settings:projects:1"
    assert keyboard_callback_data(second_page_markup)[0] == ["project:hide:/tmp/p20"]
    assert any(
        button.callback_data == "settings:projects:0"
        for row in second_page_markup.inline_keyboard
        for button in row
    )
    assert "Страница `1/2`" in first_page_text
    assert "Страница `2/2`" in second_page_text
    assert "`group/project-20`" in second_page_text


def test_render_final_text_appends_interrupted_marker() -> None:
    text = render_final_text(
        CodexResponse(
            final_text="Stopped",
            thread_id="thread-1",
            status=CodexResultStatus.INTERRUPTED,
        )
    )

    assert "(Interrupted by user)" in text


def test_render_workspace_and_detail_use_explicit_restart_and_stop_status_labels() -> None:
    interrupted_run = make_project_run(run_id=7, status=ProjectRunStatus.STOPPED_BY_USER)
    orphaned_run = make_project_run(run_id=8, status=ProjectRunStatus.ORPHANED_AFTER_RESTART)

    workspace_text = render_workspace_text(
        [
            ProjectActivitySummary(
                project_path="/tmp/api",
                project_name="api",
                is_current=True,
                latest_run=orphaned_run,
                recent_run_count=1,
            )
        ]
    )
    detail_text = render_run_detail_text(interrupted_run)

    assert "`orphaned_after_restart`" in workspace_text
    assert "Статус: `stopped_by_user`" in detail_text


def test_render_session_text_mentions_recent_projects_shortcut() -> None:
    text = render_session_text(
        cwd=Path("/tmp/api"),
        launch_mode=CodexLaunchMode.SANDBOX,
        has_session=True,
        has_active_run=False,
        recent_project_count=2,
    )

    assert "Недавние: переключение в один тап." in text


def test_render_session_text_mentions_resume_shortcut() -> None:
    text = render_session_text(
        cwd=Path("/tmp/api"),
        launch_mode=CodexLaunchMode.SANDBOX,
        has_session=True,
        has_active_run=False,
        has_resume_session=True,
    )

    assert "Быстрый старт: продолжить последнюю сессию в один тап." in text


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
        ["workspace:list:0"],
        ["nav:menu"],
    ]


def test_build_workspace_keyboard_opens_idle_project_directly() -> None:
    summary = ProjectActivitySummary(
        project_path="/tmp/cli-api",
        project_name="cli-api",
        is_current=False,
        recent_run_count=0,
    )
    markup = build_workspace_keyboard([summary])
    assert keyboard_callback_data(markup) == [
        ["repo:select:/tmp/cli-api"],
        ["workspace:list:0"],
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
    assert "⏵◉ `api` · `running`" in text
    assert "Активных процессов: `1`" in text
    assert "Проектов в выдаче: `1`" in text
    assert "🔧 Read" in text
    assert "сессия `thread-1`" in text
    assert "`⏵` активный запуск · `◉` текущий проект" in text


def test_render_workspace_text_marks_current_project_without_play_for_finished_run() -> None:
    summary = ProjectActivitySummary(
        project_path="/tmp/api",
        project_name="api",
        is_current=True,
        current_session_thread_id="thread-123",
        latest_run=make_project_run(run_id=7, status=ProjectRunStatus.STOPPED_BY_USER),
        recent_run_count=1,
    )

    text = render_workspace_text([summary])

    assert "•◉ `api` · `stopped_by_user`" in text
    assert text.count("⏵") == 1


def test_render_workspace_text_includes_idle_projects() -> None:
    summary = ProjectActivitySummary(
        project_path="/tmp/api",
        project_name="api",
        is_current=True,
        current_session_thread_id="thread-123",
        active_run=None,
        latest_run=None,
        recent_run_count=0,
    )

    text = render_workspace_text([summary])

    assert "•◉ `api` · `idle`" in text
    assert "сессия `thread-1`" in text


def test_project_run_and_detail_keyboards() -> None:
    run = make_project_run(run_id=9)
    runs_markup = build_project_runs_keyboard("/tmp/api", [run])
    detail_markup = build_run_detail_keyboard(run, user_id=42, attach_enabled=True)
    assert keyboard_callback_data(runs_markup) == [
        ["run:view:9"],
        ["run:list:/tmp/api", "workspace:list"],
        ["nav:menu"],
    ]
    assert keyboard_callback_data(detail_markup) == [
        ["run:attach:9"],
        ["action:stop:9:42"],
        ["repo:select:/tmp/api"],
        ["run:list:/tmp/api"],
        ["workspace:list"],
    ]


def test_build_workspace_keyboard_supports_pagination() -> None:
    summaries = [
        ProjectActivitySummary(
            project_path=f"/tmp/p{i}",
            project_name=f"proj-{i}",
            is_current=False,
            active_run=make_project_run(run_id=i + 1, project_path=f"/tmp/p{i}"),
            recent_run_count=1,
        )
        for i in range(12)
    ]

    first_markup = build_workspace_keyboard(summaries, page=0)
    second_markup = build_workspace_keyboard(summaries, page=1)
    first_text = render_workspace_text(summaries, page=0)
    second_text = render_workspace_text(summaries, page=1)

    assert any(button.callback_data == "workspace:list:1" for button in first_markup.inline_keyboard[-3])
    assert any(button.callback_data == "workspace:list:0" for row in second_markup.inline_keyboard for button in row)
    assert "Страница `1/2`" in first_text
    assert "Страница `2/2`" in second_text
    assert "`proj-10`" in second_text


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
