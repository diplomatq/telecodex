from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ...config import Settings
from ...models import (
    CodexLaunchMode,
    CodexResponse,
    CodexResultStatus,
    LocalCodexSession,
    ProjectActivitySummary,
    ProjectRun,
    ProjectRunStatus,
)
from ...project_labels import render_project_display_name
from ...services.status_line import CodexLimitStatus, StatusLineRenderer
from ...services.projects import ProjectVisibilityOption, RepoOption


def render_launch_mode_label(launch_mode: CodexLaunchMode) -> str:
    if launch_mode == CodexLaunchMode.FULL_ACCESS:
        return "Полный доступ"
    return "Песочница"


def render_project_header(cwd: Optional[Path]) -> str:
    project_name = render_project_display_name(cwd)
    return f"Проект: `{project_name}`"


def render_model_label(settings: Settings) -> str:
    model = settings.codex_model or "default"
    if settings.codex_reasoning_effort:
        return f"{model} {settings.codex_reasoning_effort}"
    return model


def render_runtime_footer(
    settings: Settings,
    response: Optional[CodexResponse] = None,
    *,
    include_token_summary: bool = True,
    cwd: Optional[Path] = None,
    thread_id: str = "",
    launch_mode: Optional[CodexLaunchMode | str] = None,
    limits: Optional[CodexLimitStatus] = None,
) -> str:
    return StatusLineRenderer(settings).render(
        cwd=cwd,
        response=response,
        thread_id=thread_id,
        launch_mode=launch_mode,
        limits=limits,
        include_token_summary=include_token_summary,
    )


def wrap_project_message(
    text: str,
    *,
    cwd: Optional[Path],
    settings: Settings,
    response: Optional[CodexResponse] = None,
    include_footer: bool = True,
    include_token_summary: bool = True,
    thread_id: str = "",
    launch_mode: Optional[CodexLaunchMode | str] = None,
    status_line_limits: Optional[CodexLimitStatus] = None,
) -> str:
    parts = [render_project_header(cwd), text]
    if include_footer:
        parts.append(
            render_runtime_footer(
                settings,
                response,
                include_token_summary=include_token_summary,
                cwd=cwd,
                thread_id=thread_id,
                launch_mode=launch_mode,
                limits=status_line_limits,
            )
        )
    return "\n\n".join(part for part in parts if part)


def render_home_text(cwd: Optional[Path], *, auto_created: bool = False) -> str:
    if cwd is None:
        return (
            "Быстрый доступ.\n\n"
            "Проект: `не выбран`\n"
            "Выбери существующий проект или создай новый."
        )
    created_line = f"Автоматически создал первый проект: `{render_project_display_name(cwd)}`.\n\n" if auto_created else ""
    return (
        "Быстрый доступ.\n\n"
        f"{created_line}"
        f"Проект: `{render_project_display_name(cwd)}`\n"
        "Отправь задачу или выбери действие ниже."
    )


def render_start_chat_text(
    cwd: Optional[Path],
    *,
    auto_created: bool = False,
    launch_mode: CodexLaunchMode = CodexLaunchMode.SANDBOX,
) -> str:
    if cwd is None:
        return (
            "Проект: `не выбран`\n\n"
            "Сначала создай новый проект или выбери существующий."
        )
    created_line = f"Автоматически создал первый проект: `{render_project_display_name(cwd)}`.\n\n" if auto_created else ""
    return (
        f"{created_line}"
        f"Проект: `{render_project_display_name(cwd)}`\n"
        f"Режим: `{render_launch_mode_label(launch_mode)}`\n\n"
        "Отправь задачу сообщением."
    )


def render_status_text(
    settings: Settings,
    cwd: Optional[Path],
    session: Any,
    verbose_level: int,
    *,
    auto_created: bool = False,
    launch_mode: CodexLaunchMode = CodexLaunchMode.SANDBOX,
    has_active_run: bool = False,
    active_run_count: int = 0,
    active_run_limit: int = 0,
) -> str:
    lines = []
    if auto_created and cwd is not None:
        lines.append(f"Автоматически выбран проект: `{render_project_display_name(cwd)}`")
    lines.extend(
        [
            "Статус.",
            "",
            f"Проект: `{render_project_display_name(cwd)}`",
            f"Путь: `{cwd if cwd is not None else settings.approved_directory.resolve()}`",
            f"Thread ID: `{session.thread_id if session else 'none'}`",
            f"Режим: `{render_launch_mode_label(launch_mode)}`",
            f"Модель: `{render_model_label(settings)}`",
            f"Verbose: `{verbose_level}`",
        ]
    )
    if has_active_run:
        lines.append("Запуск: `выполняется`")
    if active_run_count:
        lines.append(f"Активных процессов: `{active_run_count}`")
    if active_run_limit and active_run_count >= active_run_limit:
        lines.append(f"Лимит новых запусков: `{active_run_count}/{active_run_limit}`")
    if session and session.last_status:
        lines.append(f"Последний статус: `{session.last_status}`")
    if session and session.last_error:
        lines.append(f"Последняя ошибка: `{session.last_error[:160]}`")
    if cwd is None:
        lines.append("Выбери проект или создай новый.")
    return "\n".join(lines)


def render_session_text(
    *,
    cwd: Path,
    launch_mode: CodexLaunchMode,
    has_session: bool,
    has_active_run: bool,
    has_resume_session: bool = False,
    recent_project_count: int = 0,
    active_run_count: int = 0,
    active_run_limit: int = 0,
    auto_created: bool = False,
    notice: str = "",
) -> str:
    lines = []
    if notice:
        lines.append(notice)
        lines.append("")
    if auto_created:
        lines.append(f"Автоматически создал первый проект: `{render_project_display_name(cwd)}`.")
        lines.append("")
    lines.extend(
        [
            "Быстрый доступ.",
            "",
            f"Проект: `{render_project_display_name(cwd)}`",
            f"Режим: `{render_launch_mode_label(launch_mode)}`",
            f"Сессия: `{'текущая' if has_session else 'новая'}`",
        ]
    )
    if has_active_run:
        lines.append("Запуск: `выполняется`")
    else:
        lines.append("Выбери действие ниже или отправь задачу сообщением.")
    if has_resume_session:
        lines.append("Быстрый старт: продолжить последнюю сессию в один тап.")
    if recent_project_count >= 2:
        lines.append("Недавние: переключение в один тап.")
    if active_run_count:
        lines.append(f"Активных процессов: `{active_run_count}`")
    if active_run_limit and active_run_count >= active_run_limit:
        lines.append(f"Новые запуски временно недоступны: `{active_run_count}/{active_run_limit}`")
    return "\n".join(lines)


def render_local_sessions_text(
    *,
    cwd: Path,
    sessions: list[LocalCodexSession],
    current_thread_id: str = "",
    has_active_run: bool = False,
    active_run_count: int = 0,
    active_run_limit: int = 0,
    notice: str = "",
) -> str:
    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(
        [
            "Сессии.",
            "",
            f"Проект: `{render_project_display_name(cwd)}`",
            f"Текущая: `{current_thread_id or 'none'}`",
            "",
        ]
    )
    if sessions:
        lines.append("Выбери локальную сессию для продолжения.")
        lines.append(f"Показаны последние `{len(sessions)}`.")
    else:
        lines.append("Локальные сессии не найдены.")
    if has_active_run:
        lines.extend(["", "Сначала дождись завершения или останови текущий запуск."])
    elif active_run_count:
        lines.extend(["", f"Активных процессов у пользователя: `{active_run_count}`."])
    if active_run_limit and active_run_count >= active_run_limit:
        lines.append(f"Лимит новых запусков достигнут: `{active_run_count}/{active_run_limit}`.")
    return "\n".join(lines)


def render_verbose_text(current_level: int) -> str:
    return (
        f"Текущий verbose level: `{current_level}`\n\n"
        "0: только итог\n"
        "1: итог и token summary\n"
        "2: больше промежуточного прогресса"
    )


def _format_relative_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _format_run_duration(run: ProjectRun, *, now: Optional[datetime] = None) -> str:
    current = now or datetime.now(timezone.utc)
    started_at = run.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    end = run.finished_at or current
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return _format_relative_duration(max(int((end - started_at).total_seconds()), 0))


def _format_last_update(run: ProjectRun, *, now: Optional[datetime] = None) -> str:
    current = now or datetime.now(timezone.utc)
    last_update = run.last_update_at
    if last_update.tzinfo is None:
        last_update = last_update.replace(tzinfo=timezone.utc)
    delta_seconds = max(int((current - last_update).total_seconds()), 0)
    return _format_relative_duration(delta_seconds)


def render_repo_picker_text(
    entries: list[RepoOption],
    truncated: bool,
    *,
    auto_created: bool = False,
) -> str:
    text = "Проекты.\n\nВыбери активный проект."
    if auto_created:
        current_created = next((entry.label for entry in entries if entry.is_current), "")
        if current_created:
            text = f"Создан первый проект `{current_created}`.\n\n" + text
    if truncated:
        text += "\n\nПоказаны первые 20 проектов."
    current = next((entry.label for entry in entries if entry.is_current), "")
    if current:
        text += f"\n\nТекущий: `{current}`"
    return text


def render_project_selected_text(selected_dir: Path, base_dir: Path) -> str:
    del base_dir
    return f"Текущий проект: `{render_project_display_name(selected_dir)}`."


def render_project_created_text(project: Path) -> str:
    return f"Новый проект: `{render_project_display_name(project)}`."


def render_no_projects_text() -> str:
    return (
        "Проекты не найдены.\n\n"
        "Создай новый проект или открой список."
    )


def render_settings_text(*, current_project: Optional[Path] = None, notice: str = "") -> str:
    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(
        [
            "Настройки.",
            "",
            f"Проект: `{render_project_display_name(current_project)}`",
            "Управляй доступом к проектам и режимами бота.",
        ]
    )
    return "\n".join(lines)


def render_project_visibility_text(
    entries: list[ProjectVisibilityOption],
    *,
    page: int = 0,
    page_size: int = 20,
    notice: str = "",
) -> str:
    lines = []
    if notice:
        lines.extend([notice, ""])
    total = len(entries)
    safe_page_size = max(page_size, 1)
    max_page = max((total - 1) // safe_page_size, 0)
    current_page = min(max(page, 0), max_page)
    start = current_page * safe_page_size
    end = min(start + safe_page_size, total)
    lines.extend(
        [
            "Видимость проектов.",
            "",
            "`◉` текущий проект",
            "`🙈` скрыт",
            "",
        ]
    )
    if not entries:
        lines.append("Доступных проектов нет.")
        return "\n".join(lines)
    lines.append(f"Страница `{current_page + 1}/{max_page + 1}` · показаны `{start + 1}-{end}` из `{total}`")
    lines.append("")
    for entry in entries[start:end]:
        prefix = []
        if entry.is_current:
            prefix.append("◉")
        if entry.is_hidden:
            prefix.append("🙈")
        marker = "".join(prefix)
        if marker:
            lines.append(f"{marker} `{entry.label}`")
        else:
            lines.append(f"• `{entry.label}`")
    return "\n".join(lines)


def render_final_text(response: CodexResponse) -> str:
    if response.status == CodexResultStatus.SUCCESS:
        return response.final_text or "Готово, но Codex не вернул финальный текст."
    if response.status == CodexResultStatus.INTERRUPTED:
        base = response.final_text or "Запрос остановлен."
        if "(Interrupted by user)" not in base:
            base += "\n\n(Interrupted by user)"
        return base
    if response.status == CodexResultStatus.TIMEOUT:
        return response.final_text or "Превышено время ожидания запроса."
    if response.status == CodexResultStatus.RESUME_FAILED:
        return response.final_text or "Не удалось продолжить прошлую сессию. Попробуй ещё раз."
    if response.status == CodexResultStatus.PROTOCOL_ERROR:
        return response.final_text or "Codex вернул неожиданный ответ."
    if response.status == CodexResultStatus.CLI_ERROR:
        return response.final_text or f"Codex CLI завершился с ошибкой: {response.error_message}"
    return response.final_text or f"Request failed: {response.error_message}"


def render_run_status_label(status: ProjectRunStatus | str) -> str:
    normalized = ProjectRunStatus.from_value(status)
    if normalized == ProjectRunStatus.STOPPED_BY_USER:
        return "stopped_by_user"
    if normalized == ProjectRunStatus.ORPHANED_AFTER_RESTART:
        return "orphaned_after_restart"
    return normalized.value


def build_progress_text(
    elapsed_seconds: int,
    last_progress_lines: list[str],
    *,
    project_name: str = "",
) -> str:
    header = f"Working... {elapsed_seconds}s"
    if project_name:
        header = f"Проект: {project_name}\n\n{header}"
    if not last_progress_lines:
        return header
    return header + "\n\n" + "\n".join(last_progress_lines)


def render_launch_mode_editor_text(
    *,
    project_name: str,
    launch_mode: CodexLaunchMode,
    has_active_run: bool,
    notice: str = "",
) -> str:
    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(
        [
            "Режим доступа.",
            "",
            f"Проект: `{project_name}`",
            f"Текущий: `{render_launch_mode_label(launch_mode)}`",
            "",
            "Изменение применится к следующему запросу.",
        ]
    )
    if has_active_run:
        lines.append("Текущий запуск не изменится.")
    return "\n".join(lines)


def render_full_access_warning_text(*, project_name: str) -> str:
    return (
        "Подтверждение полного доступа.\n\n"
        f"Проект: `{project_name}`\n\n"
        "Следующие запросы будут выполняться без sandbox.\n"
        "Используй этот режим только когда нужен доступ без ограничений."
    )


def render_workspace_text(
    summaries: list[ProjectActivitySummary],
    *,
    notice: str = "",
    active_run_count: int = 0,
    active_run_limit: int = 0,
    page: int = 0,
    page_size: int = 10,
) -> str:
    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(["Сводка по проектам.", ""])
    if active_run_count:
        lines.append(f"Активных процессов: `{active_run_count}`")
    if active_run_limit and active_run_count >= active_run_limit:
        lines.append(f"Новые запуски временно недоступны: `{active_run_count}/{active_run_limit}`")
    if active_run_count or (active_run_limit and active_run_count >= active_run_limit):
        lines.append("")
    if not summaries:
        lines.append("Проекты не найдены.")
        return "\n".join(lines)
    total = len(summaries)
    if not summaries:
        lines.append("Проекты не найдены.")
        return "\n".join(lines)
    safe_page_size = max(page_size, 1)
    max_page = max((total - 1) // safe_page_size, 0)
    current_page = min(max(page, 0), max_page)
    start = current_page * safe_page_size
    end = min(start + safe_page_size, total)
    page_items = summaries[start:end]
    live_count = sum(1 for summary in summaries if summary.active_run is not None)
    error_count = sum(
        1
        for summary in summaries
        if summary.latest_run is not None
        and summary.latest_run.status in {
            ProjectRunStatus.CLI_ERROR,
            ProjectRunStatus.PROTOCOL_ERROR,
            ProjectRunStatus.TIMEOUT,
            ProjectRunStatus.RESUME_FAILED,
            ProjectRunStatus.ORPHANED_AFTER_RESTART,
        }
    )
    lines.append(f"Проектов в выдаче: `{total}`")
    lines.append(f"Live: `{live_count}` · Проблемных: `{error_count}`")
    lines.append(f"Страница `{current_page + 1}/{max_page + 1}` · показаны `{start + 1}-{end}`")
    lines.append("")
    for summary in page_items:
        run = summary.active_run or summary.latest_run
        if run is None:
            marker = "•◉" if summary.is_current else "•"
            lines.append(f"{marker} `{summary.project_name}` · `idle`")
            if summary.current_session_thread_id:
                lines.append(f"сессия `{summary.current_session_thread_id[:8]}`")
            lines.append("")
            continue
        marker = "•"
        if summary.active_run is not None:
            marker = "⏵"
        if summary.is_current:
            marker = f"{marker}◉"
        status = render_run_status_label(run.status)
        lines.append(f"{marker} `{summary.project_name}` · `{status}`")
        lines.append(
            f"длительность `{_format_run_duration(run)}` · обновление `{_format_last_update(run)}` назад"
        )
        if run.last_progress_summary:
            lines.append(run.last_progress_summary[:120])
        elif run.first_prompt_preview:
            lines.append(run.first_prompt_preview[:120])
        if summary.current_session_thread_id:
            lines.append(f"сессия `{summary.current_session_thread_id[:8]}`")
        if summary.recent_run_count:
            lines.append(f"запусков в списке: `{summary.recent_run_count}`")
        lines.append("")
    lines.append("`⏵` активный запуск · `◉` текущий проект")
    return "\n".join(lines).strip()


def render_project_runs_text(
    *,
    project_name: str,
    runs: list[ProjectRun],
    current_thread_id: str = "",
    notice: str = "",
) -> str:
    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(["Фоновые процессы.", "", f"Проект: `{project_name}`"])
    if current_thread_id:
        lines.append(f"Текущая сессия: `{current_thread_id}`")
    lines.append("")
    if not runs:
        lines.append("Запусков пока нет.")
        return "\n".join(lines)
    for run in runs:
        lines.append(
            f"#{run.run_id} · `{render_run_status_label(run.status)}` · `{_format_run_duration(run)}` · thread `{(run.thread_id or 'none')[:8]}`"
        )
        if run.last_progress_summary:
            lines.append(run.last_progress_summary[:120])
        elif run.first_prompt_preview:
            lines.append(run.first_prompt_preview[:120])
        lines.append("")
    return "\n".join(lines).strip()


def render_run_detail_text(
    run: ProjectRun,
    *,
    current_session_thread_id: str = "",
    is_current_project: bool = False,
    notice: str = "",
) -> str:
    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(
        [
            "Карточка процесса.",
            "",
            f"Проект: `{render_project_display_name(Path(run.project_path))}`",
            f"Run ID: `{run.run_id}`",
            f"Статус: `{render_run_status_label(run.status)}`",
            f"Thread ID: `{run.thread_id or 'none'}`",
            f"Длительность: `{_format_run_duration(run)}`",
            f"Последнее обновление: `{_format_last_update(run)}` назад",
            f"Текущая сессия проекта: `{current_session_thread_id or 'none'}`",
            f"Текущий проект: `{'да' if is_current_project else 'нет'}`",
        ]
    )
    if run.first_prompt_preview:
        lines.append(f"Запрос: {run.first_prompt_preview[:160]}")
    if run.last_progress_summary:
        lines.append(f"Последний шаг: {run.last_progress_summary[:160]}")
    if run.first_tool_name:
        lines.append(f"Первый инструмент: `{run.first_tool_name}`")
    if run.tool_count:
        lines.append(f"Инструментов: `{run.tool_count}`")
    if run.error_message:
        lines.append(f"Ошибка: `{run.error_message[:160]}`")
    return "\n".join(lines)
