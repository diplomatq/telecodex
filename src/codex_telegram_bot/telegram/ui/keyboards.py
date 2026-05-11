from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ...models import CodexLaunchMode, LocalCodexSession, ProjectActivitySummary, ProjectRun
from ...services.projects import ProjectVisibilityOption, RecentProjectOption, RepoOption
from .texts import render_run_status_label

LOCAL_SESSION_LABEL_LIMIT = 72


def _shorten_label(value: str, *, limit: int) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def render_local_session_button_label(session: LocalCodexSession) -> str:
    prefix = session.updated_at.strftime("%d.%m %H:%M")
    prompt = _shorten_label(session.first_prompt or session.title, limit=LOCAL_SESSION_LABEL_LIMIT)
    if not prompt:
        prompt = session.session_id[:8]
    return f"{prefix} · {prompt}"


def build_session_keyboard(
    recent_projects: list[RecentProjectOption] | None = None,
    *,
    has_resume_session: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📁 Проект", callback_data="nav:repo"),
            InlineKeyboardButton("🗂 Сессии", callback_data="session:list"),
        ],
        [
            InlineKeyboardButton("📊 Сводка", callback_data="workspace:list"),
            InlineKeyboardButton("⚙️ Режим", callback_data="mode:show"),
        ],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings:show")],
    ]
    action_row = []
    if has_resume_session:
        action_row.append(InlineKeyboardButton("▶️ Продолжить", callback_data="session:resume_current"))
    action_row.append(InlineKeyboardButton("🆕 Новая сессия", callback_data="action:new"))
    rows.append(action_row)
    if recent_projects and len(recent_projects) >= 2:
        first_row = []
        second_row = []
        for index, project in enumerate(recent_projects[:5]):
            label = f"◉ {project.label}" if project.is_current else project.label
            target_row = first_row if index < 3 else second_row
            target_row.append(
                InlineKeyboardButton(label, callback_data=f"repo:quick:{project.key}")
            )
        if first_row:
            rows.append(first_row)
        if second_row:
            rows.append(second_row)
        rows.append([InlineKeyboardButton("Ещё…", callback_data="nav:repo")])
    return InlineKeyboardMarkup(rows)


def build_navigation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")]]
    )


def build_session_transcript_keyboard(
    *,
    session_id: str,
    page: int,
    total_entries: int,
    page_size: int = 4,
) -> InlineKeyboardMarkup:
    total_pages = max((max(total_entries, 1) - 1) // max(page_size, 1), 0) + 1
    current_page = min(max(page, 0), total_pages - 1)
    rows = []
    navigation_row = []
    if current_page > 0:
        navigation_row.append(
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"session:view:{session_id}:{current_page - 1}",
            )
        )
    if current_page < total_pages - 1:
        navigation_row.append(
            InlineKeyboardButton(
                "➡️ Дальше",
                callback_data=f"session:view:{session_id}:{current_page + 1}",
            )
        )
    if navigation_row:
        rows.append(navigation_row)
    rows.append([InlineKeyboardButton("🗂 К сессиям", callback_data="session:list")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_no_project_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Создать проект", callback_data="action:create_project"),
                InlineKeyboardButton("📁 Проекты", callback_data="nav:repo"),
            ],
        ]
    )


def build_verbose_keyboard(current_level: int) -> InlineKeyboardMarkup:
    buttons = []
    for level in (0, 1, 2):
        label = f"• Verbose {level}" if level == current_level else f"Verbose {level}"
        buttons.append(InlineKeyboardButton(label, callback_data=f"verbose:set:{level}"))
    return InlineKeyboardMarkup(
        [
            buttons,
            [InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")],
        ]
    )


def build_repo_keyboard(entries: list[RepoOption]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(entry.label, callback_data=f"repo:select:{entry.key}")]
        for entry in entries
    ]
    rows.append([InlineKeyboardButton("➕ Создать проект", callback_data="action:create_project")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_local_sessions_keyboard(sessions: list[LocalCodexSession]) -> InlineKeyboardMarkup:
    rows = []
    for session in sessions:
        rows.append(
            [
                InlineKeyboardButton(
                    render_local_session_button_label(session),
                    callback_data=f"session:select:{session.session_id}",
                ),
                InlineKeyboardButton("📄", callback_data=f"session:view:{session.session_id}"),
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("🔄 Обновить", callback_data="session:refresh"),
                InlineKeyboardButton("🆕 Новая", callback_data="action:new"),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="nav:menu")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_stop_keyboard(user_id: int, *, run_id: int | None = None) -> InlineKeyboardMarkup:
    callback_data = f"action:stop:{user_id}"
    if run_id is not None:
        callback_data = f"action:stop:{run_id}:{user_id}"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏹ Остановить", callback_data=callback_data)]]
    )


def build_run_stop_keyboard(*, run_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏹ Остановить", callback_data=f"action:stop:{run_id}:{user_id}")]]
    )


def build_mode_editor_keyboard(
    launch_mode: CodexLaunchMode,
    *,
    full_access_confirmed: bool,
    back_callback: str,
) -> InlineKeyboardMarkup:
    sandbox_label = "• Песочница" if launch_mode == CodexLaunchMode.SANDBOX else "Песочница"
    if launch_mode == CodexLaunchMode.FULL_ACCESS and full_access_confirmed:
        full_access_label = "• Полный доступ"
    elif launch_mode == CodexLaunchMode.FULL_ACCESS:
        full_access_label = "Полный доступ (подтвердить)"
    else:
        full_access_label = "Полный доступ"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(sandbox_label, callback_data="mode:set:sandbox"),
                InlineKeyboardButton(full_access_label, callback_data="mode:confirm_full"),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)],
        ]
    )


def build_full_access_warning_keyboard(back_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚠️ Подтвердить полный доступ", callback_data="mode:set:full")],
            [InlineKeyboardButton("🔒 Оставить песочницу", callback_data="mode:set:sandbox")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)],
        ]
    )


def build_workspace_keyboard(
    summaries: list[ProjectActivitySummary],
    *,
    page: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    total = len(summaries)
    safe_page_size = max(page_size, 1)
    max_page = max((total - 1) // safe_page_size, 0)
    current_page = min(max(page, 0), max_page)
    start = current_page * safe_page_size
    end = start + safe_page_size
    rows = []
    for summary in summaries[start:end]:
        label = summary.project_name
        target_run = summary.active_run or summary.latest_run
        if target_run is not None:
            label = f"{label} · {render_run_status_label(target_run.status)}"
            if target_run.thread_id:
                rows.append(
                    [
                        InlineKeyboardButton(label, callback_data=f"run:attach:{target_run.run_id}"),
                        InlineKeyboardButton("ℹ️", callback_data=f"run:view:{target_run.run_id}"),
                    ]
                )
            else:
                rows.append(
                    [InlineKeyboardButton(label, callback_data=f"run:view:{target_run.run_id}")]
                )
            continue
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"repo:select:{summary.project_path}")]
        )
    navigation_row = []
    if current_page > 0:
        navigation_row.append(
            InlineKeyboardButton("⬅️ Назад", callback_data=f"workspace:list:{current_page - 1}")
        )
    if current_page < max_page:
        navigation_row.append(
            InlineKeyboardButton("➡️ Дальше", callback_data=f"workspace:list:{current_page + 1}")
        )
    if navigation_row:
        rows.append(navigation_row)
    rows.append([InlineKeyboardButton("🔄 Обновить", callback_data=f"workspace:list:{current_page}")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👁 Видимость проектов", callback_data="settings:projects")],
            [InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")],
        ]
    )


def build_project_visibility_keyboard(
    entries: list[ProjectVisibilityOption],
    *,
    page: int = 0,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    total = len(entries)
    safe_page_size = max(page_size, 1)
    max_page = max((total - 1) // safe_page_size, 0)
    current_page = min(max(page, 0), max_page)
    start = current_page * safe_page_size
    end = start + safe_page_size
    page_entries = entries[start:end]
    rows = []
    for entry in page_entries:
        if entry.is_hidden:
            label = f"👁 Показать {entry.label}"
            callback = f"project:show:{entry.key}"
        else:
            label = f"🙈 Скрыть {entry.label}"
            callback = f"project:hide:{entry.key}"
        if entry.is_current:
            label = f"◉ {label}"
        rows.append([InlineKeyboardButton(label, callback_data=callback)])
    navigation_row = []
    if current_page > 0:
        navigation_row.append(
            InlineKeyboardButton("⬅️ Назад", callback_data=f"settings:projects:{current_page - 1}")
        )
    if current_page < max_page:
        navigation_row.append(
            InlineKeyboardButton("➡️ Дальше", callback_data=f"settings:projects:{current_page + 1}")
        )
    if navigation_row:
        rows.append(navigation_row)
    rows.extend(
        [
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"settings:projects:{current_page}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="settings:show")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_project_runs_keyboard(project_key: str, runs: list[ProjectRun]) -> InlineKeyboardMarkup:
    rows = []
    for run in runs[:10]:
        rows.append(
            [
                InlineKeyboardButton(
                    f"#{run.run_id} · {render_run_status_label(run.status)}",
                    callback_data=f"run:view:{run.run_id}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("🔄 Обновить", callback_data=f"run:list:{project_key}"),
                InlineKeyboardButton("📊 Сводка", callback_data="workspace:list"),
            ],
            [InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_run_detail_keyboard(
    run: ProjectRun,
    *,
    user_id: int,
    attach_enabled: bool,
) -> InlineKeyboardMarkup:
    rows = []
    if attach_enabled:
        rows.append([InlineKeyboardButton("🔗 Сделать текущей", callback_data=f"run:attach:{run.run_id}")])
    if run.is_active:
        rows.append([InlineKeyboardButton("⏹ Остановить", callback_data=f"action:stop:{run.run_id}:{user_id}")])
    if run.thread_id:
        rows.append([InlineKeyboardButton("📄 Транскрипт", callback_data=f"session:view:{run.thread_id}")])
    rows.append([InlineKeyboardButton("📁 Открыть проект", callback_data=f"repo:select:{run.project_path}")])
    rows.append([InlineKeyboardButton("🗂 Запуски проекта", callback_data=f"run:list:{run.project_path}")])
    rows.append([InlineKeyboardButton("📊 Сводка", callback_data="workspace:list")])
    return InlineKeyboardMarkup(rows)
