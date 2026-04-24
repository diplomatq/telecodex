from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ...models import CodexLaunchMode, LocalCodexSession, ProjectActivitySummary, ProjectRun
from ...services.projects import RecentProjectOption, RepoOption


def _shorten_label(value: str, *, limit: int) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def render_local_session_button_label(session: LocalCodexSession) -> str:
    prefix = session.updated_at.strftime("%Y-%m-%d %H:%M")
    prompt = _shorten_label(session.first_prompt, limit=56)
    if not prompt:
        prompt = session.session_id[:8]
    return f"{prefix} · {prompt}"


def build_session_keyboard(recent_projects: list[RecentProjectOption] | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📁 Проект", callback_data="nav:repo"),
            InlineKeyboardButton("🗂 Сессии", callback_data="session:list"),
        ],
        [
            InlineKeyboardButton("📊 Сводка", callback_data="workspace:list"),
            InlineKeyboardButton("⚙️ Режим", callback_data="mode:show"),
        ],
        [
            InlineKeyboardButton("🆕 Новая сессия", callback_data="action:new"),
        ],
    ]
    if recent_projects and len(recent_projects) >= 2:
        shortcut_row = []
        for project in recent_projects[:3]:
            label = f"◉ {project.label}" if project.is_current else project.label
            shortcut_row.append(
                InlineKeyboardButton(label, callback_data=f"repo:quick:{project.slug}")
            )
        shortcut_row.append(InlineKeyboardButton("Ещё…", callback_data="nav:repo"))
        rows.append(shortcut_row)
    return InlineKeyboardMarkup(rows)


def build_navigation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")]]
    )


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
        [InlineKeyboardButton(entry.label, callback_data=f"repo:select:{entry.slug}")]
        for entry in entries
    ]
    rows.append([InlineKeyboardButton("➕ Создать проект", callback_data="action:create_project")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_local_sessions_keyboard(sessions: list[LocalCodexSession]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                render_local_session_button_label(session),
                callback_data=f"session:select:{session.session_id}",
            )
        ]
        for session in sessions
    ]
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


def build_workspace_keyboard(summaries: list[ProjectActivitySummary]) -> InlineKeyboardMarkup:
    rows = []
    for summary in summaries[:10]:
        label = summary.project_name
        target_run = summary.active_run or summary.latest_run
        if target_run is not None:
            label = f"{label} · {target_run.status.value}"
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
            [InlineKeyboardButton(label, callback_data=f"run:list:{summary.project_name}")]
        )
    rows.append([InlineKeyboardButton("🔄 Обновить", callback_data="workspace:list")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


def build_project_runs_keyboard(project_slug: str, runs: list[ProjectRun]) -> InlineKeyboardMarkup:
    rows = []
    for run in runs[:10]:
        rows.append(
            [
                InlineKeyboardButton(
                    f"#{run.run_id} · {run.status.value}",
                    callback_data=f"run:view:{run.run_id}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("🔄 Обновить", callback_data=f"run:list:{project_slug}"),
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
    rows.append([InlineKeyboardButton("📁 Открыть проект", callback_data=f"repo:select:{run.project_name}")])
    rows.append([InlineKeyboardButton("🗂 Запуски проекта", callback_data=f"run:list:{run.project_name}")])
    rows.append([InlineKeyboardButton("📊 Сводка", callback_data="workspace:list")])
    return InlineKeyboardMarkup(rows)
