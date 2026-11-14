from __future__ import annotations

import logging
from html import escape
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    InlineKeyboardButton as B,
    InlineKeyboardMarkup as KB,
    LinkPreviewOptions,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

log = logging.getLogger("ui")

__all__ = [
    "B", "KB", "escape", "NO_PREVIEW", "START", "HELP", "HOW_TO", "PAYOUT_NOTE",
    "CUR_NAME", "STATUS_LABEL", "HEADER", "amt", "uname", "menu_text", "main_menu",
    "topup_menu", "back_menu", "cancel_kb", "confirm_kb", "offer_text",
    "send_gift_url", "widget_url", "offer_kb", "admin_offer_kb", "send_safe",
]

START = (
    "<b>NFT-обмен</b>\n\n"
    "Здесь можно продать подарок за GRAM или Stars: "
    "отправитель создаёт оффер, вы принимаете и передаёте подарок.\n\n"
    "Выберите действие."
)

HELP = (
    "<b>Как это работает</b>\n\n"
    "1. Отправитель создаёт оффер на ваш подарок и замораживает сумму.\n"
    "2. Вы принимаете оффер и передаёте подарок по ссылке.\n"
    "3. Отправитель подтверждает получение.\n"
    "4. Выплата зачисляется на баланс в течение 1-24 часов.\n\n"
    "Если подарок не передан, оффер можно отклонить — деньги вернутся отправителю."
)

HOW_TO = (
    "<b>Как передать подарок</b>\n\n"
    "1. Нажмите кнопку «Передать NFT» — откроется отправка подарка нужному получателю.\n"
    "2. Если подарок не подставился, откройте «Страница подарка», нажмите «Передать» и введите получателя.\n"
    "3. Подтвердите отправку.\n\n"
    "После передачи вернитесь в бот и нажмите «Я передал»."
)

PAYOUT_NOTE = (
    "Выплата поступит на баланс в течение 1-24 часов "
    "в зависимости от нагрузки системы."
)

CUR_NAME = {"gram": "GRAM", "stars": "Stars"}

STATUS_LABEL = {
    "pending": "Ожидание ответа",
    "accepted": "Принято, ожидается передача подарка",
    "completed": "Получение подтверждено, ожидается выплата",
    "paid": "Завершено, выплачено",
    "rejected": "Отклонено",
    "cancelled": "Отменено",
}

HEADER = {
    "pending": "Предложение о покупке NFT",
    "accepted": "Сделка по NFT",
    "completed": "Сделка завершена",
    "paid": "Сделка завершена",
    "rejected": "Предложение отклонено",
    "cancelled": "Предложение отменено",
}

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def amt(value: int, currency: str) -> str:
    return f"{value:,}".replace(",", " ") + " " + CUR_NAME.get(currency, currency)


def uname(username: str | None, fallback: str | int = "") -> str:
    return f"@{username}" if username else str(fallback)


def menu_text(user: dict) -> str:
    return (
        "<b>Главное меню</b>\n\n"
        f"GRAM: <b>{user['gram_balance']}</b>\n"
        f"Stars: <b>{user['stars_balance']}</b>"
    )


def main_menu(user: dict) -> KB:
    has_money = user["gram_balance"] > 0 or user["stars_balance"] > 0
    kb = InlineKeyboardBuilder()
    kb.button(text="Пополнить баланс", callback_data="top")
    kb.button(
        text="Создать оффер" if has_money else "Создать оффер (нужен баланс)",
        callback_data="of:new",
    )
    kb.button(text="Мои офферы", callback_data="of:list")
    kb.button(text="Как это работает", callback_data="help")
    kb.adjust(1)
    return kb.as_markup()


def topup_menu() -> KB:
    return KB(inline_keyboard=[
        [B(text="Пополнить Stars", callback_data="top:stars")],
        [B(text="Пополнить GRAM", callback_data="top:gram")],
        [B(text="Назад", callback_data="menu")],
    ])


def back_menu() -> KB:
    return KB(inline_keyboard=[[B(text="В главное меню", callback_data="menu")]])


def cancel_kb() -> KB:
    return KB(inline_keyboard=[[B(text="Отмена", callback_data="of:new_cancel")]])


def confirm_kb() -> KB:
    return KB(inline_keyboard=[
        [B(text="Создать оффер", callback_data="of:create")],
        [B(text="Отмена", callback_data="of:new_cancel")],
    ])


def offer_text(o: dict, role: str) -> str:
    lines = [
        f"<b>{HEADER[o['status']]}</b>",
        "",
        f"Подарок: <a href=\"{o['gift_link']}\">{escape(o['gift_title'])}</a>",
        f"Сумма: <b>{amt(o['amount'], o['currency'])}</b>",
        f"Статус: {STATUS_LABEL[o['status']]}",
    ]

    if role == "creator":
        lines.append(f"Получатель: {uname(o['recipient_username'], o['recipient_id'])}")
    else:
        lines.append(f"Отправитель: {uname(o['creator_username'], o['creator_id'])}")
        lines.append(f"Номер оффера: <code>{o['id']}</code>")

    if o["status"] == "accepted" and role == "recipient":
        lines += ["", "Передайте подарок и нажмите «Я передал».", ""]
    if o["status"] == "completed":
        lines += ["", PAYOUT_NOTE, ""]
    if o["status"] == "paid":
        lines += ["", "Выплата зачислена на баланс.", ""]

    return "\n".join(lines)


def send_gift_url(username: str | None, fallback: str) -> str:
    return f"tg://send_gift?to={username or fallback}"


def widget_url(base: str, o: dict) -> str:
    status = "accepted" if o["status"] in ("completed", "paid") else o["status"]
    query = urlencode({
        "sender": o["creator_username"] or "",
        "title": o["gift_title"],
        "slug": o["gift_slug"],
        "price": o["amount"],
        "cur": o["currency"],
        "status": status,
    })
    return f"{base}{'&' if '?' in base else '?'}{query}"


def offer_kb(o: dict, role: str, webapp_base: str = "", gifts_to: str = "") -> KB | None:
    rows: list[list[B]] = []
    status = o["status"]

    if role == "recipient":
        if status == "pending":
            rows.append([B(text="Принять", callback_data=f"of:acc:{o['id']}")])
            rows.append([B(text="Отклонить", callback_data=f"of:rej:{o['id']}")])
        elif status == "accepted":
            rows.append([B(text="Передать NFT", url=send_gift_url(o["creator_username"], gifts_to))])
            rows.append([B(text="Страница подарка", url=o["gift_link"])])
            rows.append([B(text="Как передать?", callback_data=f"of:how:{o['id']}")])
            if not o.get("recipient_claimed"):
                rows.append([B(text="Я передал", callback_data=f"of:sent:{o['id']}")])
        elif status == "completed":
            rows.append([B(text="Страница подарка", url=o["gift_link"])])
        if webapp_base and status in ("pending", "accepted"):
            rows.append([B(text="Открыть виджет", web_app=WebAppInfo(url=widget_url(webapp_base, o)))])
    else:
        if status == "pending":
            rows.append([B(text="Отменить оффер", callback_data=f"of:cancel:{o['id']}")])
        if status in ("accepted", "completed"):
            rows.append([B(text="Написать получателю", url=f"tg://user?id={o['recipient_id']}")])

    return KB(inline_keyboard=rows) if rows else None


def admin_offer_kb(o: dict) -> KB:
    rows: list[list[B]] = []

    if o["status"] == "accepted":
        rows.append([B(text="Подтвердить получение", callback_data=f"adm:ok:{o['id']}")])
        rows.append([B(text="Отменить (возврат)", callback_data=f"adm:no:{o['id']}")])
    elif o["status"] == "completed":
        rows.append([B(text="Выплатить", callback_data=f"adm:pay:{o['id']}")])

    rows.append([B(text="Написать получателю", url=f"tg://user?id={o['recipient_id']}")])
    return KB(inline_keyboard=rows)


async def send_safe(bot: Bot, chat_id: int, text: str, kb: KB | None = None,
                    preview: LinkPreviewOptions | None = None) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=kb, link_preview_options=preview)
        return True
    except TelegramAPIError:
        log.warning("не доставлено user=%s", chat_id, exc_info=True)
        return False