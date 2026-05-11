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
    build_project_visibility_keyboard,
    build_repo_keyboard,
    build_run_detail_keyboard,
    build_session_transcript_keyboard,
    build_settings_keyboard,
    build_session_keyboard,
    build_verbose_keyboard,
    build_workspace_keyboard,
)
from ..telegram.ui.responder import TelegramResponder
from ..telegram.ui.texts import (
    render_home_text,
    render_no_projects_text,
    render_local_sessions_text,
    render_session_transcript_text,
    render_project_runs_text,
    render_project_created_text,
    render_project_selected_text,
    render_project_visibility_text,
    render_repo_picker_text,
    render_run_detail_text,
    render_session_text,
    render_settings_text,
    render_start_chat_text,
    render_status_text,
    render_verbose_text,
    render_workspace_text,
    render_project_display_name,
)


class NavigationFlow:
    ACTIVE_RUN_MESSAGE = PromptExecutionFlow.ACTIVE_RUN_MESSAGE
    PROJECT_VISIBILITY_PAGE_KEY = "settings_project_visibility_page"
    WORKSPACE_PAGE_KEY = "workspace_page"

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
        self.run_detail_heartbeat_seconds = 2.0
        self._live_run_detail_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
        self.workspace_heartbeat_seconds = 2.0
        self._live_workspace_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}

    @staticmethod
    def _live_run_detail_message_key(update: Update) -> tuple[int, int] | None:
        query = update.callback_query
        if query is None or query.message is None or update.effective_chat is None:
            return None
        chat_id = getattr(update.effective_chat, "id", None)
        message_id = getattr(query.message, "message_id", None)
        if chat_id is None or message_id is None:
            return None
        return (int(chat_id), int(message_id))

    def cancel_live_run_detail_monitor(self, update: Update) -> None:
        message_key = self._live_run_detail_message_key(update)
        if message_key is None:
            return
        task = self._live_run_detail_tasks.pop(message_key, None)
        if task is not None:
            task.cancel()

    def cancel_live_workspace_monitor(self, update: Update) -> None:
        message_key = self._live_run_detail_message_key(update)
        if message_key is None:
            return
        task = self._live_workspace_tasks.pop(message_key, None)
        if task is not None:
            task.cancel()

    def _start_live_workspace_monitor(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        page: int,
    ) -> None:
        message_key = self._live_run_detail_message_key(update)
        if message_key is None:
            return
        self.cancel_live_workspace_monitor(update)
        task = asyncio.create_task(
            self._live_workspace_monitor(
                update,
                context,
                request_context,
                page=page,
                message_key=message_key,
            )
        )
        self._live_workspace_tasks[message_key] = task
        task.add_done_callback(lambda completed, key=message_key: self._clear_live_workspace_task(key, completed))

    def _clear_live_workspace_task(
        self,
        message_key: tuple[int, int],
        task: asyncio.Task[None],
    ) -> None:
        current = self._live_workspace_tasks.get(message_key)
        if current is task:
            self._live_workspace_tasks.pop(message_key, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.responder.logger.debug("telegram_workspace_live_refresh_failed", error=str(exc))

    async def _live_workspace_monitor(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        page: int,
        message_key: tuple[int, int],
    ) -> None:
        while True:
            await asyncio.sleep(self.workspace_heartbeat_seconds)
            if self._live_workspace_tasks.get(message_key) is not asyncio.current_task():
                return
            project = await self.projects.resolve_current_project(context, request_context=request_context)
            project_paths = self.projects.list_project_path_strings(
                user_id=request_context.user_id if request_context is not None else None
            )
            summaries = await self.session_store.list_project_activity_summaries(
                user_id=update.effective_user.id,
                project_paths=project_paths,
                current_project_path=str(project.path) if project.path is not None else "",
            )
            active_run_count = max(
                self.execution.active_run_count(update.effective_user.id),
                sum(1 for summary in summaries if summary.active_run is not None),
            )
            text = render_workspace_text(
                summaries,
                active_run_count=active_run_count,
                active_run_limit=self.settings.max_active_runs_per_user,
                page=page,
            )
            reply_markup = build_no_project_keyboard() if not summaries else build_workspace_keyboard(summaries, page=page)
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            if not any(summary.active_run is not None for summary in summaries):
                return

    def _start_live_run_detail_monitor(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        run_id: int,
    ) -> None:
        message_key = self._live_run_detail_message_key(update)
        if message_key is None:
            return
        self.cancel_live_run_detail_monitor(update)
        task = asyncio.create_task(
            self._live_run_detail_monitor(
                update,
                context,
                request_context,
                run_id=run_id,
                message_key=message_key,
            )
        )
        self._live_run_detail_tasks[message_key] = task
        task.add_done_callback(lambda completed, key=message_key: self._clear_live_run_detail_task(key, completed))

    def _clear_live_run_detail_task(
        self,
        message_key: tuple[int, int],
        task: asyncio.Task[None],
    ) -> None:
        current = self._live_run_detail_tasks.get(message_key)
        if current is task:
            self._live_run_detail_tasks.pop(message_key, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.responder.logger.debug("telegram_run_detail_live_refresh_failed", error=str(exc))

    async def _live_run_detail_monitor(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        run_id: int,
        message_key: tuple[int, int],
    ) -> None:
        while True:
            await asyncio.sleep(self.run_detail_heartbeat_seconds)
            if self._live_run_detail_tasks.get(message_key) is not asyncio.current_task():
                return
            run = await self.session_store.get_project_run(run_id, user_id=update.effective_user.id)
            if run is None:
                return
            current_thread_id = await self.session_store.get_thread_id(
                update.effective_user.id,
                run.project_path,
            )
            current_project = await self.projects.resolve_current_project(
                context,
                request_context=request_context,
            )
            text = render_run_detail_text(
                run,
                current_session_thread_id=current_thread_id or "",
                is_current_project=(
                    str(current_project.path) == run.project_path if current_project.path else False
                ),
            )
            reply_markup = build_run_detail_keyboard(
                run,
                user_id=update.effective_user.id,
                attach_enabled=bool(run.thread_id and run.thread_id != (current_thread_id or "")),
            )
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            if not run.is_active:
                return

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
                has_resume_session=bool(session and session.thread_id),
                recent_project_count=len(recent_projects),
                active_run_count=active_run_count,
                active_run_limit=self.settings.max_active_runs_per_user,
                auto_created=project.auto_created,
                notice=notice,
            )
            reply_markup = build_session_keyboard(
                recent_projects,
                has_resume_session=bool(session and session.thread_id),
            )
        if edit:
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

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
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

    async def show_session_transcript(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        session_id: str,
        *,
        page: int = 0,
    ) -> None:
        query = update.callback_query
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            await query.answer("Сначала выбери проект.", show_alert=True)
            await self.responder.edit_ui_message(
                update,
                render_no_projects_text(),
                reply_markup=build_no_project_keyboard(),
            )
            return

        transcript = await asyncio.to_thread(
            self.execution.codex.load_session_transcript,
            project.path,
            session_id,
        )
        if transcript is None:
            await query.answer("Транскрипт не найден.", show_alert=True)
            await self.show_sessions(
                update,
                context,
                request_context,
                edit=True,
                notice="Список сессий обновлён.",
            )
            return

        await self.observability.record_event(
            "telegram_session_transcript_opened",
            request_context,
            audit_event="telegram_session_transcript_opened",
            project_path=str(project.path),
            thread_id=session_id,
            transcript_entry_count=len(transcript.entries),
        )
        await query.answer("Транскрипт")
        await self.responder.edit_ui_message(
            update,
            render_session_transcript_text(cwd=project.path, transcript=transcript, page=page),
            reply_markup=build_session_transcript_keyboard(
                session_id=session_id,
                page=page,
                total_entries=len(transcript.entries),
            ),
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
            await self.responder.edit_ui_message(
                update,
                render_no_projects_text(),
                reply_markup=build_no_project_keyboard(),
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
            title=selected.title,
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
        *,
        notice: str = "",
    ) -> None:
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        launch_mode = await self._resolve_launch_mode(update.effective_user.id, project.path)
        text = render_start_chat_text(
            project.path,
            auto_created=project.auto_created,
            launch_mode=launch_mode,
        )
        if notice:
            text = f"{notice}\n\n{text}"
        await self.responder.edit_ui_message(
            update,
            text,
            reply_markup=build_no_project_keyboard() if project.path is None else None,
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
                await self.responder.edit_ui_message(
                    update,
                    text,
                    reply_markup=build_no_project_keyboard(),
                )
            else:
                await self.responder.send_ui_message(
                    update=update,
                    text=text,
                    reply_markup=build_no_project_keyboard(),
                )
            return
        if self.execution.has_active_run_for_project(update.effective_user.id, str(project.path)):
            limit_message = self.execution.render_active_run_limit_message(update.effective_user.id)
            if update.callback_query is not None:
                await update.callback_query.answer(limit_message, show_alert=True)
            else:
                await self.responder.send_ui_message(update=update, text=limit_message)
            return

        cwd = project.path
        await self.session_store.clear_session(update.effective_user.id, str(cwd))
        notice = f"Новая сессия для `{render_project_display_name(cwd)}` готова."
        await self.show_menu(update, context, request_context, edit=edit, notice=notice)

    async def resume_current_session(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
    ) -> None:
        query = update.callback_query
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            await query.answer("Сначала выбери проект.", show_alert=True)
            await self.responder.edit_ui_message(
                update,
                render_no_projects_text(),
                reply_markup=build_no_project_keyboard(),
            )
            return
        if self.execution.has_active_run_for_project(update.effective_user.id, str(project.path)):
            await query.answer(
                self.execution.render_active_run_limit_message(update.effective_user.id),
                show_alert=True,
            )
            return
        session = await self.session_store.get_session(update.effective_user.id, str(project.path))
        if session is None or not session.thread_id:
            await query.answer("Сохранённой сессии нет.")
            await self.start_new_session(update, context, request_context, edit=True)
            return
        await query.answer("Продолжаем сессию")
        await self.show_start_chat(
            update,
            context,
            request_context,
            notice=f"Продолжаем сессию `{session.thread_id[:8]}` для проекта `{render_project_display_name(project.path)}`.",
        )

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
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

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
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

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
        await self.responder.edit_ui_message(
            update,
            render_verbose_text(level),
            reply_markup=build_verbose_keyboard(level),
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
        if args and args[0] == "new":
            if len(args) < 2:
                await self.responder.send_ui_message(
                    update=update,
                    text="Используй `/repo new <name>` или кнопку `➕ Создать проект`.",
                    reply_markup=build_no_project_keyboard(),
                )
                return
            try:
                project = await self.projects.create_project(
                    " ".join(args[1:]),
                    context=context,
                    request_context=request_context,
                )
            except Exception as exc:
                await self.responder.send_ui_message(
                    update=update,
                    text=f"Не удалось создать проект: {exc}",
                    reply_markup=build_no_project_keyboard(),
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
            try:
                candidate = self.projects.resolve_repo_slug(
                    args[0],
                    user_id=request_context.user_id if request_context is not None else None,
                )
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                await self.responder.send_ui_message(
                    update=update,
                    text=f"Проект не найден: `{args[0]}`",
                    reply_markup=build_navigation_keyboard(),
                )
                return
            context.user_data["current_directory"] = candidate
            await self.projects.remember_selected_project(request_context, candidate)
            await self.show_menu(
                update,
                context,
                request_context,
                notice=render_project_selected_text(candidate, candidate.parent),
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
        options, truncated = self.projects.list_repo_options(
            context,
            user_id=request_context.user_id if request_context is not None else None,
        )
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
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

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
            await self.responder.edit_ui_message(
                update,
                f"Не удалось создать проект: `{str(exc)[:160]}`",
                reply_markup=build_no_project_keyboard(),
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
        page: int = 0,
        notice: str = "",
    ) -> None:
        context.user_data[self.WORKSPACE_PAGE_KEY] = max(page, 0)
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        project_paths = self.projects.list_project_path_strings(
            user_id=request_context.user_id if request_context is not None else None
        )
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
            page=page,
        )
        reply_markup = build_no_project_keyboard() if not summaries else build_workspace_keyboard(summaries, page=page)
        if edit:
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            if update.callback_query is not None and any(summary.active_run is not None for summary in summaries):
                self._start_live_workspace_monitor(update, context, request_context, page=page)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

    async def show_settings(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool = True,
        notice: str = "",
    ) -> None:
        project = await self.projects.resolve_current_project(
            context,
            request_context=request_context,
            create_if_empty=False,
        )
        text = render_settings_text(current_project=project.path, notice=notice)
        reply_markup = build_settings_keyboard()
        if edit:
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

    async def show_project_visibility_settings(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        *,
        edit: bool = True,
        page: int = 0,
        notice: str = "",
    ) -> None:
        context.user_data[self.PROJECT_VISIBILITY_PAGE_KEY] = max(page, 0)
        project = await self.projects.resolve_current_project(
            context,
            request_context=request_context,
            create_if_empty=False,
        )
        entries = await self.projects.list_project_visibility_options(
            user_id=update.effective_user.id,
            current_project_path=project.path,
        )
        text = render_project_visibility_text(entries, page=page, notice=notice)
        reply_markup = build_project_visibility_keyboard(entries, page=page)
        if edit:
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

    async def set_project_visibility_from_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request_context,
        project_key: str,
        *,
        hidden: bool,
    ) -> None:
        query = update.callback_query
        page = int(context.user_data.get(self.PROJECT_VISIBILITY_PAGE_KEY, 0))
        try:
            project_path = self.projects.resolve_repo_key(
                project_key,
                user_id=update.effective_user.id,
                include_hidden=True,
            )
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            await query.answer("Проект недоступен.", show_alert=True)
            await self.show_project_visibility_settings(update, context, request_context, edit=True)
            return
        await self.projects.set_project_hidden_state(
            user_id=update.effective_user.id,
            project_path=project_path,
            hidden=hidden,
        )
        action_text = "скрыт" if hidden else "показан"
        await query.answer(f"Проект {action_text}")
        await self.show_project_visibility_settings(
            update,
            context,
            request_context,
            edit=True,
            page=page,
            notice=f"Проект `{render_project_display_name(project_path)}` {action_text}.",
        )

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
        project_path = self.projects.resolve_repo_key(
            project_slug,
            user_id=update.effective_user.id,
            include_hidden=True,
        )
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
            project_name=render_project_display_name(project_path),
            runs=runs,
            current_thread_id=current_thread_id or "",
            notice=notice,
        )
        reply_markup = build_project_runs_keyboard(str(project_path), runs)
        if edit:
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

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
                await self.responder.send_ui_message(update=update, text="Процесс не найден.")
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
            await self.responder.edit_ui_message(update, text, reply_markup=reply_markup)
            if run.is_active and update.callback_query is not None:
                self._start_live_run_detail_monitor(
                    update,
                    context,
                    request_context,
                    run_id=run_id,
                )
            return
        await self.responder.send_ui_message(update=update, text=text, reply_markup=reply_markup)

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
            title=self.execution.codex.build_session_title(run.first_prompt_preview),
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
            notice=f"Сессия `{run.thread_id[:8]}` выбрана для проекта `{render_project_display_name(project_path)}`.",
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
            selected_dir = self.projects.resolve_repo_key(
                slug,
                user_id=update.effective_user.id,
            )
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            await query.answer("Project unavailable.", show_alert=True)
            options, truncated = self.projects.list_repo_options(
                context,
                user_id=request_context.user_id if request_context is not None else None,
            )
            await self.responder.edit_ui_message(
                update,
                render_repo_picker_text(options, truncated) if options else render_no_projects_text(),
                reply_markup=build_repo_keyboard(options) if options else build_no_project_keyboard(),
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
            notice=render_project_selected_text(selected_dir, selected_dir.parent),
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
            selected_dir = self.projects.resolve_repo_key(
                slug,
                user_id=update.effective_user.id,
            )
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
            notice=render_project_selected_text(selected_dir, selected_dir.parent),
        )
