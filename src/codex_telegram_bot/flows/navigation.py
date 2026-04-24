from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Settings
from ..models import CodexLaunchMode
from .execution import PromptExecutionFlow
from ..services.observability import ObservabilityService
from ..services.projects import ProjectService
from ..services.status_line import StatusLineRenderer
from ..session_store import SessionStore
from ..telegram.ui.keyboards import (
    build_navigation_keyboard,
    build_local_sessions_keyboard,
    build_no_project_keyboard,
    build_project_runs_keyboard,
    build_repo_keyboard,
    build_run_detail_keyboard,
    build_session_keyboard,
    build_verbose_keyboard,
    build_workspace_keyboard,
)
from ..telegram.ui.responder import TelegramResponder
from ..telegram.ui.texts import (
    render_home_text,
    render_no_projects_text,
    render_local_sessions_text,
    render_project_runs_text,
    render_project_created_text,
    render_project_selected_text,
    render_repo_picker_text,
    render_run_detail_text,
    render_session_text,
    render_start_chat_text,
    render_status_text,
    render_verbose_text,
    render_workspace_text,
)


class NavigationFlow:
    ACTIVE_RUN_MESSAGE = PromptExecutionFlow.ACTIVE_RUN_MESSAGE

    def __init__(
        self,
        settings: Settings,
        session_store: SessionStore,
        projects: ProjectService,
        observability: ObservabilityService,
        responder: TelegramResponder,
        execution: Any,
    ):
        self.settings = settings
        self.session_store = session_store
        self.projects = projects
        self.observability = observability
        self.responder = responder
        self.execution = execution

    async def _resolve_launch_mode(self, user_id: int, cwd) -> CodexLaunchMode:
        if cwd is None:
            return CodexLaunchMode.from_value(self.settings.codex_default_launch_mode)
        stored = await self.session_store.get_project_launch_mode(user_id, str(cwd))
        if stored is not None:
            return stored
        return CodexLaunchMode.from_value(self.settings.codex_default_launch_mode)

    async def show_home(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
    ) -> None:
        await self.show_menu(update, context, request_context)

    async def show_menu(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool = False,
        notice: str = "",
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            text = render_no_projects_text()
            reply_markup = build_no_project_keyboard()
        else:
            session = await self.session_store.get_session(update.effective_user.id, str(project.path))
            launch_mode = await self._resolve_launch_mode(update.effective_user.id, project.path)
            recent_projects = await self.projects.list_recent_repo_options(
                user_id=update.effective_user.id,
                current_project_path=project.path,
                limit=3,
            )
            active_run_count = self.execution.active_run_count(update.effective_user.id)
            text = render_session_text(
                cwd=project.path,
                launch_mode=launch_mode,
                has_session=session is not None,
                has_active_run=self.execution.has_active_run_for_project(
                    update.effective_user.id,
                    str(project.path),
                ),
                recent_project_count=len(recent_projects),
                active_run_count=active_run_count,
                active_run_limit=self.settings.max_active_runs_per_user,
                auto_created=project.auto_created,
                notice=notice,
            )
            reply_markup = build_session_keyboard(recent_projects)
        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )

    async def show_sessions(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool = False,
        notice: str = "",
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            text = render_no_projects_text()
            reply_markup = build_no_project_keyboard()
        else:
            sessions = await asyncio.to_thread(
                self.execution.codex.discover_local_sessions,
                project.path,
                limit=10,
            )
            current_thread_id = await self.session_store.get_thread_id(
                update.effective_user.id,
                str(project.path),
            )
            await self.observability.record_event(
                "telegram_sessions_opened",
                request_context,
                audit_event="telegram_sessions_opened",
                project_path=str(project.path),
                session_count=len(sessions),
            )
            text = render_local_sessions_text(
                cwd=project.path,
                sessions=sessions,
                current_thread_id=current_thread_id or "",
                has_active_run=self.execution.has_active_run_for_project(
                    update.effective_user.id,
                    str(project.path),
                ),
                active_run_count=self.execution.active_run_count(update.effective_user.id),
                active_run_limit=self.settings.max_active_runs_per_user,
                notice=notice,
            )
            reply_markup = build_local_sessions_keyboard(sessions)
        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )

    async def select_session_from_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        session_id: str,
    ) -> None:
        query = update.callback_query
        user_id = update.effective_user.id

        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            await query.answer("Сначала выбери проект.", show_alert=True)
            await self.responder.edit_callback_message(
                update,
                render_no_projects_text(),
                reply_markup=build_no_project_keyboard(),
                parse_mode="Markdown",
            )
            return
        if self.execution.has_active_run_for_project(user_id, str(project.path)):
            await query.answer(
                self.execution.render_active_run_limit_message(user_id),
                show_alert=True,
            )
            return

        sessions = await asyncio.to_thread(
            self.execution.codex.discover_local_sessions,
            project.path,
            limit=None,
        )
        selected = next((session for session in sessions if session.session_id == session_id), None)
        if selected is None:
            await query.answer("Сессия не найдена.", show_alert=True)
            await self.show_sessions(update, context, request_context, edit=True)
            return

        await self.session_store.upsert_session(
            user_id,
            str(project.path),
            selected.session_id,
            last_status="selected",
        )
        await self.observability.record_event(
            "telegram_session_selected",
            request_context,
            audit_event="telegram_session_selected",
            event_status="selected",
            project_path=str(project.path),
            thread_id=selected.session_id,
        )
        await query.answer("Сессия выбрана")
        await self.show_menu(
            update,
            context,
            request_context,
            edit=True,
            notice=f"Выбрана сессия `{selected.session_id[:8]}`.",
        )

    async def show_start_chat(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        launch_mode = await self._resolve_launch_mode(update.effective_user.id, project.path)
        await self.responder.edit_callback_message(
            update,
            render_start_chat_text(
                project.path,
                auto_created=project.auto_created,
                launch_mode=launch_mode,
            ),
            reply_markup=build_no_project_keyboard() if project.path is None else None,
            parse_mode="Markdown",
        )

    async def start_new_session(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool = False,
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            text = "Сначала создай проект."
            if edit:
                await self.responder.edit_callback_message(
                    update,
                    text,
                    reply_markup=build_no_project_keyboard(),
                    parse_mode="Markdown",
                )
            else:
                await update.effective_message.reply_text(
                    text,
                    reply_markup=build_no_project_keyboard(),
                    parse_mode="Markdown",
                )
            return
        if self.execution.has_active_run_for_project(update.effective_user.id, str(project.path)):
            limit_message = self.execution.render_active_run_limit_message(update.effective_user.id)
            if update.callback_query is not None:
                await update.callback_query.answer(limit_message, show_alert=True)
            else:
                await update.effective_message.reply_text(limit_message, parse_mode="Markdown")
            return

        cwd = project.path
        await self.session_store.clear_session(update.effective_user.id, str(cwd))
        notice = f"Новая сессия для `{cwd.name}` готова."
        await self.show_menu(update, context, request_context, edit=edit, notice=notice)

    async def show_status(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool = False,
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        cwd = project.path
        launch_mode = await self._resolve_launch_mode(update.effective_user.id, cwd)
        session = (
            await self.session_store.get_session(update.effective_user.id, str(cwd))
            if cwd is not None
            else None
        )
        verbose = int(context.user_data.get("verbose_level", self.settings.verbose_level))
        text = render_status_text(
            self.settings,
            cwd,
            session,
            verbose,
            auto_created=project.auto_created,
            launch_mode=launch_mode,
            has_active_run=(
                cwd is not None
                and self.execution.has_active_run_for_project(update.effective_user.id, str(cwd))
            ),
            active_run_count=self.execution.active_run_count(update.effective_user.id),
            active_run_limit=self.settings.max_active_runs_per_user,
        )
        status_line_limits = None
        if (
            self.settings.status_line_enabled
            and StatusLineRenderer.needs_limit_status(self.settings.status_line_template)
        ):
            status_line_limits = await self.execution.limit_status_provider.get_status(
                cwd=cwd,
                thread_id=session.thread_id if session else "",
            )
        status_line = StatusLineRenderer(self.settings).render(
            cwd=cwd,
            thread_id=session.thread_id if session else "",
            launch_mode=launch_mode,
            limits=status_line_limits,
        )
        if status_line:
            text = f"{text}\n\n{status_line}"
        reply_markup = build_no_project_keyboard() if cwd is None else None
        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )

    async def show_verbose(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        edit: bool = False,
    ) -> None:
        current = int(context.user_data.get("verbose_level", self.settings.verbose_level))
        text = render_verbose_text(current)
        reply_markup = build_verbose_keyboard(current)
        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )

    async def set_verbose(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        level: int,
    ) -> None:
        previous_level = int(context.user_data.get("verbose_level", self.settings.verbose_level))
        context.user_data["verbose_level"] = level
        await self.observability.record_event(
            "telegram_verbose_selected",
            request_context,
            audit_event="telegram_verbose_selected",
            previous_verbose_level=previous_level,
            new_verbose_level=level,
        )
        await self.responder.edit_callback_message(
            update,
            render_verbose_text(level),
            reply_markup=build_verbose_keyboard(level),
            parse_mode="Markdown",
        )

    async def handle_repo_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        command_text: str,
    ) -> None:
        args = command_text.split()[1:]
        base = self.settings.approved_directory.resolve()
        if args and args[0] == "new":
            if len(args) < 2:
                await update.effective_message.reply_text(
                    "Используй `/repo new <name>` или кнопку `➕ Создать проект`.",
                    reply_markup=build_no_project_keyboard(),
                    parse_mode="Markdown",
                )
                return
            try:
                project = await self.projects.create_project(
                    " ".join(args[1:]),
                    context=context,
                    request_context=request_context,
                )
            except Exception as exc:
                await update.effective_message.reply_text(
                    f"Не удалось создать проект: {exc}",
                    reply_markup=build_no_project_keyboard(),
                    parse_mode="Markdown",
                )
                return
            await self.show_menu(
                update,
                context,
                request_context,
                notice=render_project_created_text(project),
            )
            return
        if args:
            candidate = (base / args[0]).resolve()
            self.projects.ensure_in_workspace(candidate)
            if not candidate.exists() or not candidate.is_dir():
                await update.effective_message.reply_text(
                    f"Проект не найден: `{candidate.name}`",
                    reply_markup=build_navigation_keyboard(),
                    parse_mode="Markdown",
                )
                return
            context.user_data["current_directory"] = candidate
            await self.projects.remember_selected_project(request_context, candidate)
            await self.show_menu(
                update,
                context,
                request_context,
                notice=render_project_selected_text(candidate, base),
            )
            return

        await self.show_repo_picker(update, context, request_context, edit=False)

    async def show_repo_picker(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool,
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        options, truncated = self.projects.list_repo_options(context)
        await self.observability.record_event(
            "telegram_repo_picker_opened",
            request_context,
            audit_event="telegram_repo_picker_opened",
            project_count=len(options),
            truncated=truncated,
        )
        if not options:
            text = render_no_projects_text()
            reply_markup = build_no_project_keyboard()
        else:
            text = render_repo_picker_text(options, truncated, auto_created=project.auto_created)
            reply_markup = build_repo_keyboard(options)

        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )

    async def create_project_from_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
    ) -> None:
        query = update.callback_query
        try:
            project = await self.projects.create_project(
                None,
                context=context,
                request_context=request_context,
                auto=True,
            )
        except Exception as exc:
            await query.answer("Create failed.", show_alert=True)
            await self.responder.edit_callback_message(
                update,
                f"Не удалось создать проект: `{str(exc)[:160]}`",
                reply_markup=build_no_project_keyboard(),
                parse_mode="Markdown",
            )
            return
        await query.answer(f"Создан {project.name}")
        await self.show_menu(
            update,
            context,
            request_context,
            edit=True,
            notice=render_project_created_text(project),
        )

    async def show_workspace(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool = False,
        notice: str = "",
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        project_paths = self.projects.list_project_path_strings()
        summaries = await self.session_store.list_project_activity_summaries(
            user_id=update.effective_user.id,
            project_paths=project_paths,
            current_project_path=str(project.path) if project.path is not None else "",
        )
        await self.observability.record_event(
            "telegram_workspace_opened",
            request_context,
            audit_event="telegram_workspace_opened",
            project_count=len(project_paths),
            summary_count=len(summaries),
        )
        active_run_count = max(
            self.execution.active_run_count(update.effective_user.id),
            sum(1 for summary in summaries if summary.active_run is not None),
        )
        text = render_workspace_text(
            summaries,
            notice=notice,
            active_run_count=active_run_count,
            active_run_limit=self.settings.max_active_runs_per_user,
        )
        reply_markup = build_no_project_keyboard() if not summaries else build_workspace_keyboard(summaries)
        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    async def show_project_runs(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        project_slug: str,
        *,
        edit: bool = True,
        notice: str = "",
    ) -> None:
        project_path = self.projects.resolve_repo_slug(project_slug)
        runs = await self.session_store.list_project_runs(
            user_id=update.effective_user.id,
            project_path=str(project_path),
            limit=10,
        )
        current_thread_id = await self.session_store.get_thread_id(
            update.effective_user.id,
            str(project_path),
        )
        text = render_project_runs_text(
            project_name=project_path.name,
            runs=runs,
            current_thread_id=current_thread_id or "",
            notice=notice,
        )
        reply_markup = build_project_runs_keyboard(project_path.name, runs)
        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    async def show_run_detail(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        run_id: int,
        *,
        edit: bool = True,
        notice: str = "",
    ) -> None:
        run = await self.session_store.get_project_run(run_id, user_id=update.effective_user.id)
        if run is None:
            if update.callback_query is not None:
                await update.callback_query.answer("Процесс не найден.", show_alert=True)
                await self.show_workspace(update, context, request_context, edit=True)
            else:
                await update.effective_message.reply_text("Процесс не найден.")
            return
        current_thread_id = await self.session_store.get_thread_id(update.effective_user.id, run.project_path)
        current_project = await self.projects.resolve_current_project(context, request_context=request_context)
        text = render_run_detail_text(
            run,
            current_session_thread_id=current_thread_id or "",
            is_current_project=str(current_project.path) == run.project_path if current_project.path else False,
            notice=notice,
        )
        reply_markup = build_run_detail_keyboard(
            run,
            user_id=update.effective_user.id,
            attach_enabled=bool(run.thread_id and run.thread_id != (current_thread_id or "")),
        )
        if edit:
            await self.responder.edit_callback_message(
                update,
                text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
            return
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    async def attach_run_to_project(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        run_id: int,
    ) -> None:
        run = await self.session_store.get_project_run(run_id, user_id=update.effective_user.id)
        if run is None or not run.thread_id:
            await update.callback_query.answer("Процесс нельзя выбрать.", show_alert=True)
            return
        project_path = Path(run.project_path)
        context.user_data["current_directory"] = project_path
        await self.projects.remember_selected_project(request_context, project_path)
        await self.session_store.upsert_session(
            update.effective_user.id,
            run.project_path,
            run.thread_id,
            last_status="selected",
        )
        await self.observability.record_event(
            "telegram_run_attached",
            request_context,
            audit_event="telegram_run_attached",
            event_status="selected",
            run_id=run_id,
            thread_id=run.thread_id,
            project_path=run.project_path,
        )
        await update.callback_query.answer("Сессия выбрана")
        await self.show_run_detail(
            update,
            context,
            request_context,
            run_id,
            edit=True,
            notice=f"Сессия `{run.thread_id[:8]}` выбрана для проекта `{project_path.name}`.",
        )

    async def select_repo_from_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        slug: str,
    ) -> None:
        query = update.callback_query
        current_resolution = await self.projects.resolve_current_project(
            context,
            request_context=request_context,
            create_if_empty=False,
        )
        previous_dir = current_resolution.path or self.settings.approved_directory.resolve()
        try:
            selected_dir = self.projects.resolve_repo_slug(slug)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            await query.answer("Project unavailable.", show_alert=True)
            options, truncated = self.projects.list_repo_options(context)
            await self.responder.edit_callback_message(
                update,
                render_repo_picker_text(options, truncated) if options else render_no_projects_text(),
                reply_markup=build_repo_keyboard(options) if options else build_no_project_keyboard(),
                parse_mode="Markdown",
            )
            return
        context.user_data["current_directory"] = selected_dir
        await self.projects.remember_selected_project(request_context, selected_dir)
        await self.observability.record_event(
            "telegram_repo_selected",
            request_context,
            audit_event="telegram_repo_selected",
            previous_project=previous_dir.name,
            selected_project=selected_dir.name,
        )
        await query.answer(f"Переключено: {selected_dir.name}")
        await self.show_menu(
            update,
            context,
            request_context,
            edit=True,
            notice=render_project_selected_text(selected_dir, self.settings.approved_directory),
        )

    async def quick_select_repo_from_menu(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        slug: str,
    ) -> None:
        query = update.callback_query
        current_resolution = await self.projects.resolve_current_project(
            context,
            request_context=request_context,
            create_if_empty=False,
        )
        previous_dir = current_resolution.path or self.settings.approved_directory.resolve()
        try:
            selected_dir = self.projects.resolve_repo_slug(slug)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            await query.answer("Проект недоступен.", show_alert=True)
            await self.show_menu(
                update,
                context,
                request_context,
                edit=True,
                notice="Список недавних проектов обновлён.",
            )
            return

        context.user_data["current_directory"] = selected_dir
        await self.projects.remember_selected_project(request_context, selected_dir)
        await self.observability.record_event(
            "telegram_repo_selected",
            request_context,
            audit_event="telegram_repo_selected",
            previous_project=previous_dir.name,
            selected_project=selected_dir.name,
            selection_source="menu_recent",
        )
        await query.answer(f"Переключено: {selected_dir.name}")
        await self.show_menu(
            update,
            context,
            request_context,
            edit=True,
            notice=render_project_selected_text(selected_dir, self.settings.approved_directory),
        )
