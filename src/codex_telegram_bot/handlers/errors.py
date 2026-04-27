from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ..services.observability import ObservabilityService
from ..telegram.ui.responder import TelegramResponder


class ErrorHandlers:
    def __init__(self, observability: ObservabilityService, logger: Any):
        self.observability = observability
        self.logger = logger
        self.responder = TelegramResponder(logger)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        request_context = None
        if isinstance(update, Update):
            request_context = self.observability.make_request_context(
                update,
                context,
                source="handler_error",
            )
        await self.observability.record_event(
            "telegram_handler_error",
            request_context,
            audit_event="request_failed" if request_context else None,
            event_status="handler_error",
            error_message=str(context.error),
            level="error",
        )
        if isinstance(update, Update) and update.effective_message:
            try:
                await self.responder.send_ui_message(
                    update=update,
                    text=f"Error: {context.error}",
                )
            except Exception:
                self.logger.exception("telegram_error_reply_failed")
