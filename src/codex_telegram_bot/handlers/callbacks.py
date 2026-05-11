from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from ..flows.execution import PromptExecutionFlow
from ..flows.navigation import NavigationFlow
from ..models import CodexLaunchMode
from ..services.observability import ObservabilityService
from ..telegram.ui.keyboards import build_no_project_keyboard
from ..telegram.ui.texts import render_no_projects_text


class CallbackHandlers:
    def __init__(
        self,
        navigation: NavigationFlow,
        execution: PromptExecutionFlow,
        observability: ObservabilityService,
    ):
        self.navigation = navigation
        self.execution = execution
        self.observability = observability

    async def stop_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = self.observability.make_request_context(
            update,
            context,
            source="command",
            command_name="stop",
        )
        await self.observability.record_event("telegram_update_received", request_context)
        await self.execution.stop_callback(update, request_context)

    async def handle_ui_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        request_context = self.observability.make_request_context(update, context, source="command")
        await self.observability.record_event("telegram_update_received", request_context)
        await self.observability.record_event(
            "telegram_callback_received",
            request_context,
            audit_event="telegram_callback_received",
            callback_data=query.data,
        )
        if not await self.observability.ensure_authorized(update, request_context):
            await query.answer("Access denied.", show_alert=True)
            return

        self.navigation.cancel_live_run_detail_monitor(update)
        self.navigation.cancel_live_workspace_monitor(update)
        data = query.data or ""
        if data in {"nav:menu", "nav:controls"}:
            await query.answer("Menu")
            await self.navigation.show_menu(update, context, request_context, edit=True)
            return

        if data == "settings:show":
            await query.answer("Настройки")
            await self.navigation.show_settings(update, context, request_context, edit=True)
            return

        if data == "settings:projects":
            await query.answer("Проекты")
            await self.navigation.show_project_visibility_settings(
                update,
                context,
                request_context,
                edit=True,
            )
            return

        if data.startswith("settings:projects:"):
            page = int(data.rsplit(":", 1)[1])
            await query.answer("Проекты")
            await self.navigation.show_project_visibility_settings(
                update,
                context,
                request_context,
                edit=True,
                page=page,
            )
            return

        if data == "nav:start":
            await self.observability.record_event(
                "telegram_nav_start",
                request_context,
                audit_event="telegram_nav_start",
            )
            await query.answer()
            await self.navigation.show_start_chat(update, context, request_context)
            return

        if data == "nav:status":
            await query.answer()
            await self.navigation.show_status(update, context, request_context, edit=True)
            return

        if data in {"nav:repo", "repo:list", "repo:refresh"}:
            await query.answer("Projects")
            await self.navigation.show_repo_picker(update, context, request_context, edit=True)
            return

        if data == "workspace:list":
            await query.answer("Сводка")
            await self.navigation.show_workspace(update, context, request_context, edit=True)
            return

        if data.startswith("workspace:list:"):
            page = int(data.rsplit(":", 1)[1])
            await query.answer("Сводка")
            await self.navigation.show_workspace(update, context, request_context, edit=True, page=page)
            return

        if data in {"session:list", "session:refresh"}:
            await query.answer("Сессии")
            await self.navigation.show_sessions(update, context, request_context, edit=True)
            return

        if data == "session:resume_current":
            await self.navigation.resume_current_session(update, context, request_context)
            return

        if data.startswith("run:list:"):
            slug = data.split(":", 2)[2]
            await query.answer("Запуски")
            await self.navigation.show_project_runs(update, context, request_context, slug, edit=True)
            return

        if data.startswith("run:view:"):
            run_id = int(data.split(":", 2)[2])
            await query.answer("Процесс")
            await self.navigation.show_run_detail(update, context, request_context, run_id, edit=True)
            return

        if data.startswith("run:attach:"):
            run_id = int(data.split(":", 2)[2])
            await self.navigation.attach_run_to_project(update, context, request_context, run_id)
            return

        if data.startswith("session:select:"):
            session_id = data.split(":", 2)[2]
            await self.navigation.select_session_from_callback(
                update,
                context,
                request_context,
                session_id,
            )
            return

        if data.startswith("session:view:"):
            parts = data.split(":", 3)
            session_id = parts[2]
            page = int(parts[3]) if len(parts) > 3 else 0
            await self.navigation.show_session_transcript(
                update,
                context,
                request_context,
                session_id,
                page=page,
            )
            return

        if data == "action:new":
            project = await self.navigation.projects.resolve_current_project(
                context,
                request_context=request_context,
            )
            if project.path is None:
                await query.answer("Create a project first.", show_alert=True)
                await self.navigation.responder.edit_ui_message(
                    update,
                    render_no_projects_text(),
                    reply_markup=build_no_project_keyboard(),
                )
                return
            await self.observability.record_event(
                "telegram_command_new",
                request_context,
                audit_event="command_new",
            )
            await query.answer("New session")
            await self.navigation.start_new_session(update, context, request_context, edit=True)
            return

        if data == "action:create_project":
            await self.navigation.create_project_from_callback(update, context, request_context)
            return

        if data == "verbose:show":
            await query.answer("Verbose")
            await self.navigation.show_verbose(update, context, edit=True)
            return

        if data == "mode:show":
            await query.answer("Launch mode")
            await self.execution.show_mode_editor(update, context, edit=True)
            return

        if data == "mode:set:sandbox":
            await query.answer("Sandbox")
            await self.execution.set_launch_mode(update, context, CodexLaunchMode.SANDBOX)
            return

        if data == "mode:confirm_full":
            await query.answer("Full access")
            await self.execution.confirm_full_access(update, context)
            return

        if data == "mode:set:full":
            await query.answer("Full access enabled")
            await self.execution.enable_full_access(update, context)
            return

        if data == "mode:start":
            await query.answer("Запуск теперь происходит автоматически.", show_alert=True)
            return

        if data in {"mode:cancel", "mode:pending"}:
            await query.answer("Отложенного запуска больше нет.", show_alert=True)
            return

        if data.startswith("verbose:set:"):
            level = int(data.rsplit(":", 1)[1])
            await query.answer(f"Verbose {level}")
            await self.navigation.set_verbose(update, context, request_context, level)
            return

        if data.startswith("repo:select:"):
            slug = data.split(":", 2)[2]
            await self.navigation.select_repo_from_callback(update, context, request_context, slug)
            return

        if data.startswith("repo:quick:"):
            slug = data.split(":", 2)[2]
            await self.navigation.quick_select_repo_from_menu(
                update,
                context,
                request_context,
                slug,
            )
            return

        if data.startswith("project:hide:"):
            project_key = data.split(":", 2)[2]
            await self.navigation.set_project_visibility_from_callback(
                update,
                context,
                request_context,
                project_key,
                hidden=True,
            )
            return

        if data.startswith("project:show:"):
            project_key = data.split(":", 2)[2]
            await self.navigation.set_project_visibility_from_callback(
                update,
                context,
                request_context,
                project_key,
                hidden=False,
            )
            return

        await query.answer("Unknown action.", show_alert=True)
