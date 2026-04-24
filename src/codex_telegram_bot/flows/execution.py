from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..codex_runner import CodexRunner
from ..config import Settings
from ..models import (
    CodexLaunchMode,
    CodexResponse,
    CodexResultStatus,
    ProjectRun,
    ProjectRunStatus,
    CodexStreamEvent,
    PreparedCodexRequest,
)
from ..rate_limiter import RateLimiter
from ..services.observability import ObservabilityService
from ..services.projects import ProjectService
from ..services.status_line import CodexLimitStatusProvider, StatusLineRenderer
from ..session_store import SessionStore
from ..telegram.ui.keyboards import (
    build_full_access_warning_keyboard,
    build_mode_editor_keyboard,
    build_no_project_keyboard,
    build_stop_keyboard,
)
from ..telegram.ui.responder import TelegramResponder
from ..telegram.ui.texts import (
    build_progress_text,
    render_final_text,
    render_full_access_warning_text,
    render_launch_mode_editor_text,
    render_launch_mode_label,
    render_no_projects_text,
    render_project_display_name,
    wrap_project_message,
)


@dataclass
class ActiveRunHandle:
    run_id: int
    user_id: int
    project_path: str
    interrupt_event: asyncio.Event


class PromptExecutionFlow:
    ACTIVE_RUN_MESSAGE = (
        "Достигнут лимит активных запусков. Открой `/workspace`, "
        "чтобы переключиться на один из процессов или остановить его."
    )

    def __init__(
        self,
        settings: Settings,
        session_store: SessionStore,
        codex: CodexRunner,
        rate_limiter: RateLimiter,
        projects: ProjectService,
        observability: ObservabilityService,
        responder: TelegramResponder,
        logger: Any,
        limit_status_provider: Optional[CodexLimitStatusProvider] = None,
    ):
        self.settings = settings
        self.session_store = session_store
        self.codex = codex
        self.rate_limiter = rate_limiter
        self.projects = projects
        self.observability = observability
        self.responder = responder
        self.logger = logger
        self.limit_status_provider = limit_status_provider or CodexLimitStatusProvider(
            settings,
            logger,
        )
        self.typing_heartbeat_seconds = 4.0
        self.progress_heartbeat_seconds = 2.0
        self.active_interrupts: dict[int, asyncio.Event] = {}
        self.active_runs: dict[int, ActiveRunHandle] = {}
        self.active_runs_by_user: dict[int, dict[int, ActiveRunHandle]] = {}

    @staticmethod
    def _mode_changed_notice(launch_mode: CodexLaunchMode) -> str:
        return (
            f"Режим доступа изменён на `{render_launch_mode_label(launch_mode)}`. "
            "Следующие запросы в этом проекте будут использовать его."
        )

    async def resolve_launch_mode(
        self,
        *,
        user_id: int,
        project_path: Optional[Path],
    ) -> CodexLaunchMode:
        if project_path is None:
            return CodexLaunchMode.from_value(self.settings.codex_default_launch_mode)
        stored = await self.session_store.get_project_launch_mode(user_id, str(project_path))
        if stored is not None:
            return stored
        return CodexLaunchMode.from_value(self.settings.codex_default_launch_mode)

    def active_run_count(self, user_id: int) -> int:
        return len(self.active_runs_by_user.get(user_id, {}))

    def has_active_run(self, user_id: int) -> bool:
        return self.active_run_count(user_id) > 0

    def render_active_run_limit_message(self, user_id: int) -> str:
        active_count = self.active_run_count(user_id)
        limit = self.settings.max_active_runs_per_user
        return (
            f"Достигнут лимит активных запусков: `{active_count}/{limit}`.\n\n"
            "Открой `/workspace`, чтобы переключиться на один из процессов или остановить его."
        )

    def has_active_run_for_project(self, user_id: int, project_path: str) -> bool:
        runs = self.active_runs_by_user.get(user_id, {})
        return any(handle.project_path == project_path for handle in runs.values())

    def get_active_run(self, run_id: int) -> Optional[ActiveRunHandle]:
        return self.active_runs.get(run_id)

    def _register_active_run(self, handle: ActiveRunHandle) -> None:
        self.active_runs[handle.run_id] = handle
        runs = self.active_runs_by_user.setdefault(handle.user_id, {})
        runs[handle.run_id] = handle
        self.active_interrupts[handle.user_id] = handle.interrupt_event

    def _unregister_active_run(self, run_id: int) -> None:
        handle = self.active_runs.pop(run_id, None)
        if handle is None:
            return
        runs = self.active_runs_by_user.get(handle.user_id)
        if runs is not None:
            runs.pop(run_id, None)
            if runs:
                next_handle = next(reversed(runs.values()))
                self.active_interrupts[handle.user_id] = next_handle.interrupt_event
            else:
                self.active_runs_by_user.pop(handle.user_id, None)
                self.active_interrupts.pop(handle.user_id, None)

    async def stop_run(self, *, user_id: int, run_id: int) -> bool:
        handle = self.active_runs.get(run_id)
        if handle is None or handle.user_id != user_id:
            return False
        handle.interrupt_event.set()
        await self.session_store.update_project_run(run_id, stop_requested=True)
        return True

    async def show_mode_editor(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        edit: bool,
        notice: str = "",
    ) -> None:
        request_context = self.observability.make_request_context(update, context, source="command")
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            text = render_no_projects_text()
            reply_markup = build_no_project_keyboard()
        else:
            launch_mode = await self.resolve_launch_mode(
                user_id=update.effective_user.id,
                project_path=project.path,
            )
            text = render_launch_mode_editor_text(
                project_name=render_project_display_name(project.path),
                launch_mode=launch_mode,
                has_active_run=self.has_active_run(update.effective_user.id),
                notice=notice,
            )
            reply_markup = build_mode_editor_keyboard(
                launch_mode,
                full_access_confirmed=launch_mode == CodexLaunchMode.FULL_ACCESS,
                back_callback="nav:menu",
            )
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

    async def set_launch_mode(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        launch_mode: CodexLaunchMode,
    ) -> None:
        request_context = self.observability.make_request_context(update, context, source="command")
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            await self.responder.edit_callback_message(
                update,
                render_no_projects_text(),
                reply_markup=build_no_project_keyboard(),
                parse_mode="Markdown",
            )
            return
        await self.session_store.set_project_launch_mode(
            update.effective_user.id,
            str(project.path),
            launch_mode,
        )
        await self.show_mode_editor(
            update,
            context,
            edit=True,
            notice=self._mode_changed_notice(launch_mode),
        )

    async def confirm_full_access(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        request_context = self.observability.make_request_context(update, context, source="command")
        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            await self.responder.edit_callback_message(
                update,
                render_no_projects_text(),
                reply_markup=build_no_project_keyboard(),
                parse_mode="Markdown",
            )
            return
        await self.responder.edit_callback_message(
            update,
            render_full_access_warning_text(project_name=render_project_display_name(project.path)),
            reply_markup=build_full_access_warning_keyboard("mode:show"),
            parse_mode="Markdown",
        )

    async def enable_full_access(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await self.set_launch_mode(update, context, CodexLaunchMode.FULL_ACCESS)

    async def run_prepared_prompt(
        self,
        *,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        prepared_request: PreparedCodexRequest,
        request_context,
    ) -> None:
        user_id = update.effective_user.id
        if self.active_run_count(user_id) >= self.settings.max_active_runs_per_user:
            limit_message = self.render_active_run_limit_message(user_id)
            await self.observability.record_event(
                "codex_request_rejected",
                request_context,
                audit_event="request_rejected",
                event_status="active_run",
                active_run_count=self.active_run_count(user_id),
                active_run_limit=self.settings.max_active_runs_per_user,
            )
            await update.effective_message.reply_text(limit_message, parse_mode="Markdown")
            await self._cleanup_paths(prepared_request.cleanup_paths)
            return
        if not self.rate_limiter.allow(user_id):
            await self.observability.record_event(
                "codex_request_rate_limited",
                request_context,
                audit_event="request_failed",
                event_status="rate_limited",
            )
            await update.effective_message.reply_text("Rate limit exceeded. Please wait a bit.")
            await self._cleanup_paths(prepared_request.cleanup_paths)
            return

        project = await self.projects.resolve_current_project(context, request_context=request_context)
        if project.path is None:
            await self.observability.record_event(
                "project_create_failed",
                request_context,
                audit_event="project_create_failed",
                event_status="no_project_selected",
            )
            await update.effective_message.reply_text(
                render_no_projects_text(),
                reply_markup=build_no_project_keyboard(),
                parse_mode="Markdown",
            )
            await self._cleanup_paths(prepared_request.cleanup_paths)
            return

        cwd = project.path
        launch_mode = await self.resolve_launch_mode(user_id=user_id, project_path=cwd)
        request_context.cwd = str(cwd)
        request_context.launch_mode = launch_mode.value
        if project.auto_created:
            await update.effective_message.reply_text(
                f"Создал и выбрал первый проект: `{render_project_display_name(cwd)}`.",
                parse_mode="Markdown",
            )

        previous_thread_id = await self.resolve_previous_thread_id(
            user_id=user_id,
            cwd=cwd,
            request_context=request_context,
        )
        request_context.has_previous_thread = bool(previous_thread_id)

        await self.observability.record_event(
            "codex_request_started",
            request_context,
            audit_event="request_started",
            event_status="started",
            thread_id=previous_thread_id or "",
        )

        run_id = await self.session_store.create_project_run(
            user_id=user_id,
            project_path=str(cwd),
            thread_id=previous_thread_id or "",
            first_prompt_preview=self._build_prompt_preview(prepared_request.prompt),
        )
        await self.observability.record_event(
            "codex_run_created",
            request_context,
            audit_event="codex_run_created",
            event_status="running",
            run_id=run_id,
            thread_id=previous_thread_id or "",
        )

        interrupt_event = asyncio.Event()
        self._register_active_run(
            ActiveRunHandle(
                run_id=run_id,
                user_id=user_id,
                project_path=str(cwd),
                interrupt_event=interrupt_event,
            )
        )
        stop_markup = build_stop_keyboard(user_id, run_id=run_id)
        request_started_at = time.monotonic()
        await update.effective_chat.send_action(ChatAction.TYPING)
        progress = await update.effective_message.reply_text(
            build_progress_text(0, [], project_name=render_project_display_name(cwd)),
            reply_markup=stop_markup,
        )
        request_finished = asyncio.Event()

        last_progress_lines: list[str] = []
        tool_count = 0
        first_tool = ""
        saw_text_delta = False

        typing_task = asyncio.create_task(
            self.typing_heartbeat(
                chat=update.effective_chat,
                request_finished=request_finished,
                request_context=request_context,
            )
        )
        progress_task = asyncio.create_task(
            self.progress_heartbeat(
                progress=progress,
                stop_markup=stop_markup,
                run_id=run_id,
                request_finished=request_finished,
                request_started_at=request_started_at,
                last_progress_lines=last_progress_lines,
                interrupt_event=interrupt_event,
                request_context=request_context,
            )
        )

        async def on_event(event: CodexStreamEvent) -> None:
            nonlocal last_progress_lines, tool_count, first_tool, saw_text_delta
            if event.tool_call:
                tool_count += 1
                if not first_tool:
                    first_tool = event.tool_call.name
                    await self.observability.record_event(
                        "codex_request_progress",
                        request_context,
                        first_tool=first_tool,
                        tool_count=tool_count,
                    )
                line = f"🔧 {event.tool_call.name}"
            elif event.text_delta:
                saw_text_delta = True
                if int(context.user_data.get("verbose_level", self.settings.verbose_level)) >= 2:
                    line = f"💬 {event.text_delta[-100:]}"
                else:
                    return
            elif event.text_snapshot:
                snippet = event.text_snapshot.strip().splitlines()[0][:100]
                line = f"💬 {snippet}" if snippet else ""
            elif event.usage:
                await self.observability.record_event(
                    "codex_request_progress",
                    request_context,
                    tool_count=tool_count,
                    saw_text_delta=saw_text_delta,
                    input_tokens=event.usage.get("input_tokens", 0),
                    cached_input_tokens=event.usage.get("cached_input_tokens", 0),
                    output_tokens=event.usage.get("output_tokens", 0),
                )
                return
            else:
                return

            if not line:
                return

            verbose_level = int(context.user_data.get("verbose_level", self.settings.verbose_level))
            if verbose_level == 0:
                await self.session_store.update_project_run(
                    run_id,
                    thread_id=event.thread_id or None,
                    last_progress_summary=line,
                    first_tool_name=first_tool or None,
                    tool_count=tool_count,
                )
                return

            last_progress_lines.append(line)
            last_progress_lines = last_progress_lines[-12:]
            await self.session_store.update_project_run(
                run_id,
                thread_id=event.thread_id or None,
                last_progress_summary=line,
                first_tool_name=first_tool or None,
                tool_count=tool_count,
            )
            try:
                await progress.edit_text(
                    build_progress_text(
                        int(time.monotonic() - request_started_at),
                        last_progress_lines,
                        project_name=render_project_display_name(cwd),
                    ),
                    reply_markup=stop_markup if not interrupt_event.is_set() else None,
                )
            except Exception:
                self.logger.debug(
                    "telegram_progress_edit_failed",
                    **self.observability.context_fields(request_context),
                )

        try:
            response = await self.codex.run(
                prompt=prepared_request.prompt,
                cwd=cwd,
                launch_mode=launch_mode,
                previous_thread_id=previous_thread_id,
                on_event=on_event,
                interrupt_event=interrupt_event,
                image_paths=prepared_request.image_paths or None,
            )
        except Exception as exc:
            self._unregister_active_run(run_id)
            self.logger.exception(
                "codex_request_failed_exception",
                **self.observability.context_fields(request_context),
            )
            await self.session_store.update_project_run(
                run_id,
                status=ProjectRunStatus.CLI_ERROR,
                error_message=str(exc),
                finished=True,
            )
            if previous_thread_id:
                await self.session_store.update_session_result(
                    user_id,
                    str(cwd),
                    last_status="exception",
                    last_error=str(exc),
                )
            await self.observability.record_event(
                "codex_request_failed",
                request_context,
                audit_event="request_failed",
                event_status="exception",
                error_message=str(exc),
                level="error",
            )
            try:
                request_finished.set()
                typing_task.cancel()
                progress_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing_task
                with contextlib.suppress(asyncio.CancelledError):
                    await progress_task
                await progress.delete()
            except Exception:
                self.logger.debug(
                    "telegram_progress_delete_failed",
                    **self.observability.context_fields(request_context),
                )
            await update.effective_message.reply_text(f"Request failed: {exc}")
            await self._cleanup_paths(prepared_request.cleanup_paths)
            return
        finally:
            request_finished.set()
            typing_task.cancel()
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task
            self._unregister_active_run(run_id)

        if response.fallback_reason:
            await self.observability.record_event(
                "codex_resume_fallback_used",
                request_context,
                audit_event="resume_fallback",
                event_status="used",
                fallback_reason=response.fallback_reason,
                thread_id=response.thread_id,
            )

        await self.persist_session_result(
            user_id=user_id,
            project_path=str(cwd),
            previous_thread_id=previous_thread_id,
            response=response,
        )
        await self.session_store.update_project_run(
            run_id,
            thread_id=response.thread_id or previous_thread_id or "",
            status=ProjectRunStatus.from_value(response.status.value),
            last_progress_summary=last_progress_lines[-1] if last_progress_lines else "",
            first_tool_name=first_tool or "",
            tool_count=tool_count,
            error_message=response.error_message,
            finished=True,
        )
        await self.observability.record_event(
            "codex_run_finished",
            request_context,
            audit_event="codex_run_finished",
            event_status=str(response.status),
            run_id=run_id,
            thread_id=response.thread_id or previous_thread_id or "",
            tool_count=tool_count,
        )

        try:
            await progress.delete()
        except Exception:
            self.logger.debug(
                "telegram_progress_delete_failed",
                **self.observability.context_fields(request_context),
            )

        if response.status == CodexResultStatus.INTERRUPTED:
            await self.observability.record_event(
                "codex_user_interrupt_completed",
                request_context,
                audit_event="request_interrupted",
                event_status=str(response.status),
                **self.observability.response_fields(response),
            )
        elif response.status == CodexResultStatus.SUCCESS:
            await self.observability.record_event(
                "codex_request_finished",
                request_context,
                audit_event="request_finished",
                event_status=str(response.status),
                **self.observability.response_fields(response),
            )
        else:
            await self.observability.record_event(
                "codex_request_failed",
                request_context,
                audit_event="request_failed",
                event_status=str(response.status),
                level="error",
                **self.observability.response_fields(response),
            )

        status_line_limits = None
        if (
            self.settings.status_line_enabled
            and StatusLineRenderer.needs_limit_status(self.settings.status_line_template)
        ):
            status_line_limits = await self.limit_status_provider.get_status(
                cwd=cwd,
                thread_id=response.thread_id or previous_thread_id or "",
            )

        final_text = wrap_project_message(
            render_final_text(response),
            cwd=cwd,
            settings=self.settings,
            response=response,
            thread_id=response.thread_id or previous_thread_id or "",
            launch_mode=launch_mode,
            status_line_limits=status_line_limits,
            include_token_summary=(
                int(context.user_data.get("verbose_level", self.settings.verbose_level)) >= 1
            ),
        )

        await self.responder.send_final_response(
            update=update,
            markdown_text=final_text,
        )
        await self._cleanup_paths(prepared_request.cleanup_paths)

    async def persist_session_result(
        self,
        *,
        user_id: int,
        project_path: str,
        previous_thread_id: Optional[str],
        response: CodexResponse,
    ) -> None:
        if response.thread_id:
            await self.session_store.upsert_session(
                user_id,
                project_path,
                response.thread_id,
                last_status=str(response.status),
                last_error=response.error_message,
            )
            return
        if previous_thread_id:
            await self.session_store.update_session_result(
                user_id,
                project_path,
                last_status=str(response.status),
                last_error=response.error_message,
            )

    async def resolve_previous_thread_id(
        self,
        *,
        user_id: int,
        cwd: Path,
        request_context,
    ) -> Optional[str]:
        stored_thread_id = await self.session_store.get_thread_id(user_id, str(cwd))
        if stored_thread_id:
            return stored_thread_id

        reset_at_unix = await self.session_store.get_session_reset_at_unix(user_id, str(cwd))
        discovered_thread_id = await asyncio.to_thread(
            self.codex.discover_latest_session_id,
            cwd,
            modified_after=reset_at_unix,
        )
        if not discovered_thread_id:
            return None

        await self.session_store.upsert_session(
            user_id,
            str(cwd),
            discovered_thread_id,
            last_status="discovered",
        )
        await self.observability.record_event(
            "codex_session_discovered",
            request_context,
            audit_event="session_discovered",
            event_status="discovered",
            thread_id=discovered_thread_id,
        )
        return discovered_thread_id

    async def stop_callback(self, update: Update, request_context) -> None:
        query = update.callback_query
        parts = query.data.split(":")
        run_id: Optional[int] = None
        target_user = int(parts[-1])
        if len(parts) >= 4 and parts[-2].isdigit():
            run_id = int(parts[-2])
            target_user = int(parts[-1])
        interrupt = None
        if run_id is not None:
            handle = self.active_runs.get(run_id)
            interrupt = handle.interrupt_event if handle and handle.user_id == target_user else None
        else:
            interrupt = self.active_interrupts.get(target_user)
        await self.observability.record_event(
            "codex_user_interrupt_requested",
            request_context,
            target_user=target_user,
            run_id=run_id,
            has_active_interrupt=interrupt is not None,
        )
        if query.from_user.id != target_user:
            await query.answer("You can only stop your own request.", show_alert=True)
            return
        if interrupt is None:
            await query.answer("Already finished.")
            return
        interrupt.set()
        if run_id is not None:
            await self.session_store.update_project_run(run_id, stop_requested=True)
        await self.observability.record_event(
            "request_interrupted",
            request_context,
            audit_event="request_interrupted",
            event_status="requested",
            target_user=target_user,
            run_id=run_id,
        )
        await query.answer("Stopping...")

    async def typing_heartbeat(
        self,
        *,
        chat: Any,
        request_finished: asyncio.Event,
        request_context,
    ) -> None:
        while not request_finished.is_set():
            try:
                await asyncio.sleep(self.typing_heartbeat_seconds)
                if request_finished.is_set():
                    return
                await chat.send_action(ChatAction.TYPING)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.debug(
                    "telegram_typing_send_failed",
                    **self.observability.context_fields(request_context),
                )

    async def progress_heartbeat(
        self,
        *,
        progress: Any,
        stop_markup,
        run_id: int | None = None,
        request_finished: asyncio.Event,
        request_started_at: float,
        last_progress_lines: list[str],
        interrupt_event: asyncio.Event,
        request_context,
    ) -> None:
        while not request_finished.is_set():
            try:
                await asyncio.sleep(self.progress_heartbeat_seconds)
                if request_finished.is_set():
                    return
                if run_id is not None:
                    run = await self.session_store.get_project_run(
                        run_id,
                        user_id=request_context.user_id,
                    )
                    if run is not None and run.stop_requested and not interrupt_event.is_set():
                        interrupt_event.set()
                await progress.edit_text(
                    build_progress_text(
                        int(time.monotonic() - request_started_at),
                        last_progress_lines,
                        project_name=(
                            render_project_display_name(Path(request_context.cwd))
                            if request_context.cwd
                            else ""
                        ),
                    ),
                    reply_markup=stop_markup if not interrupt_event.is_set() else None,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc)
                if "Message is not modified" in message:
                    self.logger.debug(
                        "telegram_progress_noop",
                        **self.observability.context_fields(request_context),
                    )
                else:
                    self.logger.debug(
                        "telegram_progress_edit_failed",
                        **self.observability.context_fields(request_context),
                    )

    async def _cleanup_paths(self, paths: list[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                self.logger.warning("photo_tempfile_cleanup_failed", path=str(path))

    @staticmethod
    def _build_prompt_preview(prompt: str) -> str:
        text = " ".join(prompt.strip().split())
        return text[:160]
