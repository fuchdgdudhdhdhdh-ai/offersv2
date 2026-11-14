from __future__ import annotations

import asyncio
import logging
import sys

import logging_setup

logging_setup.setup("INFO")

from aiohttp import web  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import BotCommand, ErrorEvent, LinkPreviewOptions, Update  # noqa: E402

import db  # noqa: E402
import ui  # noqa: E402
from config import load  # noqa: E402
from handlers_admin import router as admin_router  # noqa: E402
from handlers_user import handle_widget_query, router as user_router  # noqa: E402
from middleware import ContextMiddleware  # noqa: E402

log = logging.getLogger("main")
cfg = load()

bot = Bot(cfg.bot_token, default=DefaultBotProperties(
    parse_mode=ParseMode.HTML,
    link_preview=LinkPreviewOptions(is_disabled=True),
))
dp = Dispatcher(storage=MemoryStorage())
dp["cfg"] = cfg
dp["bot"] = bot

dp.update.outer_middleware(ContextMiddleware())
dp.include_router(admin_router)
dp.include_router(user_router)

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="help", description="Как это работает"),
    BotCommand(command="cancel", description="Отменить ввод"),
]


@dp.errors()
async def on_error(event: ErrorEvent) -> bool:
    log.exception("ошибка в обработчике: %s", event.exception)
    text = f"<b>Ошибка</b>\n<code>{type(event.exception).__name__}: {event.exception}</code>"
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text[:3800])
        except Exception:
            log.warning("не смог уведомить админа %s", admin_id, exc_info=True)
    return True


async def feed(update: Update) -> None:
    try:
        await dp.feed_update(bot, update)
    except Exception:
        log.exception("сбой обработки update=%s", update.update_id)


async def webhook(request: web.Request) -> web.Response:
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != cfg.webhook_secret:
        log.warning("неверный secret от %s", request.remote)
        raise web.HTTPUnauthorized()
    try:
        payload = await request.json()
    except Exception:
        log.exception("невалидный JSON")
        raise web.HTTPBadRequest()
    try:
        update = Update.model_validate(payload, context={"bot": bot})
    except Exception:
        log.exception("невалидный Update: %s", payload)
        return web.json_response({"ok": True})
    log.debug("update %s", update.update_id)
    if "widget_query" in payload:
        async def process_widget_query(query: dict) -> None:
            try:
                await handle_widget_query(query, bot, cfg)
            except Exception:
                log.exception("ошибка обработки widget_query")
                query_id = str(query.get("id") or "")
                if query_id:
                    await ui.answer_widget_query(bot, query_id, "Внутренняя ошибка. Попробуйте ещё раз.")
        asyncio.create_task(process_widget_query(payload["widget_query"]))
    else:
        asyncio.create_task(feed(update))
    return web.json_response({"ok": True})


@web.middleware
async def access_log(request: web.Request, handler):
    try:
        response = await handler(request)
        log.debug("%s %s -> %s", request.method, request.path, response.status)
        return response
    except web.HTTPException:
        raise
    except Exception:
        log.exception("сбой запроса %s %s", request.method, request.path)
        raise web.HTTPInternalServerError()


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "mode": "webhook" if cfg.webhook_base else "polling"})


async def on_startup(app: web.Application) -> None:
    await bot.set_my_commands(COMMANDS)
    url = f"{cfg.webhook_base}/tg/{cfg.webhook_secret}"
    allowed_updates = dp.resolve_used_update_types()
    if "widget_query" not in allowed_updates:
        allowed_updates.append("widget_query")
    await bot.set_webhook(
        url,
        secret_token=cfg.webhook_secret,
        allowed_updates=allowed_updates,
        drop_pending_updates=True,
    )
    log.info("webhook: %s", url)


async def on_shutdown(app: web.Application) -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        log.warning("delete_webhook не удался", exc_info=True)
    await bot.session.close()
    db.close()


def build_app() -> web.Application:
    app = web.Application(middlewares=[access_log])
    app.router.add_post(f"/tg/{cfg.webhook_secret}", webhook)
    app.router.add_get("/health", health)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


async def main() -> None:
    db.connect(cfg.db_path)
    if not cfg.webhook_base:
        log.warning("WEBHOOK_BASE_URL пуст — работаю в режиме polling")
        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        return

    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.port)
    await site.start()
    log.info("слушаю порт %s", cfg.port)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("остановлено")
    except Exception:
        log.exception("фатальная ошибка")
        sys.exit(1)