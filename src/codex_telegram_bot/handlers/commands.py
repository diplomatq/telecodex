from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from ..flows.execution import PromptExecutionFlow
from ..flows.navigation import NavigationFlow
from ..preflight import render_preflight_report, run_preflight
from ..services.observability import ObservabilityService
from ..telegram.ui.keyboards import build_verbose_keyboard
from ..telegram.ui.responder import TelegramResponder
from ..telegram.ui.texts import render_verbose_text


class CommandHandlers:
    def __init__(
        self,
        navigation: NavigationFlow,
        execution: PromptExecutionFlow,
        observability: ObservabilityService,
    ):
        self.navigation = navigation
        self.execution = execution
        self.observability = observability
        self.responder = TelegramResponder(observability.logger)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="start",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_start",
            request_context,
            audit_event="command_start",
        )
        await self.navigation.show_home(update, context, request_context)

    async def menu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="menu",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_menu",
            request_context,
            audit_event="command_menu",
        )
        await self.navigation.show_menu(update, context, request_context)

    async def mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="mode",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_mode",
            request_context,
            audit_event="command_mode",
        )
        await self.execution.show_mode_editor(update, context, edit=False)

    async def new_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="new",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_new",
            request_context,
            audit_event="command_new",
        )
        await self.navigation.start_new_session(update, context, request_context)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="status",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_status",
            request_context,
            audit_event="command_status",
        )
        await self.navigation.show_status(update, context, request_context)

    async def sessions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="sessions",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_sessions",
            request_context,
            audit_event="command_sessions",
        )
        await self.navigation.show_sessions(update, context, request_context)

    async def workspace_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="workspace",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_workspace",
            request_context,
            audit_event="command_workspace",
        )
        await self.navigation.show_workspace(update, context, request_context)

    async def health_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="health",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        report = await run_preflight(
            settings=self.navigation.settings,
            store=self.navigation.session_store,
            codex_cli_validator=self.execution.codex.validate_cli_available,
            finalize_orphaned_runs=False,
        )
        await self.observability.record_event(
            "telegram_command_health",
            request_context,
            audit_event="command_health",
            event_status="ok" if report.ok else "failed",
            orphaned_run_count=report.orphaned_run_count,
            errors=report.errors,
        )
        await self.responder.send_ui_message(
            update=update,
            text=render_preflight_report(report, sqlite_path=self.navigation.settings.sqlite_path),
        )

    async def verbose_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="verbose",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event("telegram_command_verbose", request_context)
        parts = (update.effective_message.text or "").split()
        if len(parts) == 1:
            await self.navigation.show_verbose(update, context)
            return
        try:
            level = int(parts[1])
            if level not in (0, 1, 2):
                raise ValueError
        except ValueError:
            current = int(context.user_data.get("verbose_level", self.navigation.settings.verbose_level))
            await self.responder.send_ui_message(
                update=update,
                text="Используй `/verbose 0|1|2` или выбери уровень кнопкой ниже.",
                reply_markup=build_verbose_keyboard(current),
            )
            return
        context.user_data["verbose_level"] = level
        await self.responder.send_ui_message(
            update=update,
            text=render_verbose_text(level),
            reply_markup=build_verbose_keyboard(level),
        )

    async def repo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="repo",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        if not await self.observability.ensure_authorized(update, request_context):
            return
        await self.observability.record_event(
            "telegram_command_repo",
            request_context,
            audit_event="command_repo",
        )
        await self.navigation.handle_repo_command(
            update,
            context,
            request_context,
            command_text=update.effective_message.text or "",
        )
