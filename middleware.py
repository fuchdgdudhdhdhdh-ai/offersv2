from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

import db

log = logging.getLogger("mw")


class ContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: User | None = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        user = await db.upsert_user(tg_user.id, tg_user.username, tg_user.full_name)
        data["user"] = user

        if user["banned"]:
            from aiogram.types import CallbackQuery

            if isinstance(event, CallbackQuery):
                await event.answer("Вы забанены", show_alert=True)
            elif data.get("event_update"):
                log.debug("забанен user=%s", tg_user.id)
            return None

        log.debug("update=%s user=%s type=%s", data.get("event_update").update_id if data.get("event_update") else "-",
                  tg_user.id, type(event).__name__)
        return await handler(event, data)