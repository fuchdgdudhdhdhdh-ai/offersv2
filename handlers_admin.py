#епт

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
import ui
from config import Config

log = logging.getLogger("admin")
router = Router(name="admin")


def is_admin(user_id: int, cfg: Config) -> bool:
    return user_id in cfg.admin_ids


class Adm(StatesGroup):
    find = State()
    balance = State()


def panel_kb(s: dict) -> object:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"Подтвердить получение ({s['queue_confirm']})", callback_data="adm:queue")
    kb.button(text=f"Ожидают выплаты ({s['queue_payout']})", callback_data="adm:payouts")
    kb.button(text="Пользователи", callback_data="adm:users:0")
    kb.button(text="Найти пользователя", callback_data="adm:find")
    kb.button(text="Офферы", callback_data="adm:offers")
    kb.button(text="Статистика", callback_data="adm:stats")
    kb.adjust(1)
    return kb.as_markup()


async def guard(c: CallbackQuery, cfg: Config) -> bool:
    if not is_admin(c.from_user.id, cfg):
        await c.answer("Только для админов", show_alert=True)
        return False
    return True


@router.message(Command("admin"))
async def admin_panel(m: Message, cfg: Config) -> None:
    if not is_admin(m.from_user.id, cfg):
        return
    s = await db.stats()
    await m.answer(
        f"<b>Админ-панель</b>\n\nНа подтверждении: {s['queue_confirm']} · К выплате: {s['queue_payout']}",
        reply_markup=panel_kb(s),
    )


@router.callback_query(F.data == "adm:home")
async def home(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    s = await db.stats()
    await c.message.edit_text(
        f"<b>Админ-панель</b>\n\nНа подтверждении: {s['queue_confirm']} · К выплате: {s['queue_payout']}",
        reply_markup=panel_kb(s),
    )
    await c.answer()


@router.callback_query(F.data == "adm:stats")
async def stats(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    s = await db.stats()
    await c.message.edit_text(
        "<b>Статистика</b>\n\n"
        f"Пользователей: {s['users']} (бан: {s['banned']})\n"
        f"Офферов всего: {s['offers']}\n"
        f"В ожидании: {s['pending']}\n"
        f"На подтверждении: {s['queue_confirm']}\n"
        f"К выплате: {s['queue_payout']}\n"
        f"Выплачено всего: {s['paid_sum']}\n"
        f"Stars на балансах: {s['stars_total']}\n"
        f"GRAM на балансах: {s['gram_total']}",
        reply_markup=ui.KB(inline_keyboard=[[ui.B(text="Назад", callback_data="adm:home")]]),
    )
    await c.answer()


# ---------- очередь подтверждений ----------

@router.callback_query(F.data == "adm:queue")
async def queue(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    rows = await db.list_offers(status="accepted", limit=20)
    kb = InlineKeyboardBuilder()
    for o in rows:
        mark = " · сообщил" if o["recipient_claimed"] else ""
        kb.button(
            text=f"#{o['id']} @{o['recipient_username'] or o['recipient_id']} "
                 f"{ui.amt(o['amount'], o['currency'])}{mark}",
            callback_data=f"adm:off:{o['id']}",
        )
    kb.button(text="Назад", callback_data="adm:home")
    kb.adjust(1)
    await c.message.edit_text(
        f"<b>Ожидают подтверждения</b> ({len(rows)})\nМетка «сообщил» — получатель нажал «Я передал».",
        reply_markup=kb.as_markup(),
    )
    await c.answer()



@router.callback_query(F.data.startswith("adm:off:"))
async def offer_card(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return

    o = await db.get_offer(int(c.data.split(":")[2]))
    if not o:
        return await c.answer("Не найден", show_alert=True)

    await c.message.edit_text(
        f"<b>Оффер #{o['id']}</b>\n\n"
        f"От: {ui.uname(o['creator_username'], o['creator_id'])}\n"
        f"Кому: {ui.uname(o['recipient_username'], o['recipient_id'])}\n"
        f"Подарок: <a href=\"{o['gift_link']}\">{ui.escape(o['gift_title'])}</a>\n"
        f"Сумма: <b>{ui.amt(o['amount'], o['currency'])}</b>\n"
        f"Статус: {ui.STATUS_LABEL[o['status']]}\n"
        f"«Я передал»: {'да' if o['recipient_claimed'] else 'нет'}",
        reply_markup=ui.KB(inline_keyboard=[
            *(
                [[ui.B(text="Подтвердить получение", callback_data=f"adm:ok:{o['id']}")]]
                if o["status"] == "accepted" else []
            ),
            *(
                [[ui.B(text="Выплатить", callback_data=f"adm:pay:{o['id']}")]]
                if o["status"] == "completed" else []
            ),
            *(
                [[ui.B(text="Отменить (возврат)", callback_data=f"adm:no:{o['id']}")]]
                if o["status"] in ("accepted", "completed") else []
            ),
            [ui.B(text="Написать получателю", url=f"tg://user?id={o['recipient_id']}")],
            [ui.B(text="Назад", callback_data="adm:queue")],
        ]),
    )

    await c.answer()


@router.callback_query(F.data.startswith("adm:ok:"))
async def confirm_gift(c: CallbackQuery, cfg: Config, bot: Bot) -> None:
    if not await guard(c, cfg):
        return

    o = await db.complete_offer(int(c.data.split(":")[2]))
    if not o:
        return await c.answer("Уже обработано", show_alert=True)

    await ui.send_safe(
        bot,
        o["recipient_id"],
        f"Получение подарка по офферу #{o['id']} подтверждено. Сделка завершена.\n\n{ui.PAYOUT_NOTE}"
    )

    await ui.send_safe(
        bot,
        o["creator_id"],
        f"Получение подарка по офферу #{o['id']} подтверждено. "
        f"{ui.amt(o['amount'], o['currency'])} ждут выплаты получателю."
    )

    await c.message.edit_text(
        f"Подтверждено. Оффер #{o['id']} ждёт выплаты.",
        reply_markup=ui.KB(inline_keyboard=[
            [ui.B(text="К очереди", callback_data="adm:queue")]
        ])
    )

    await c.answer("Готово")


@router.callback_query(F.data == "adm:payouts")
async def payouts(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    rows = await db.list_offers(status="completed", limit=20)
    kb = InlineKeyboardBuilder()
    for o in rows:
        kb.button(text=f"Выплатить #{o['id']} {ui.amt(o['amount'], o['currency'])}",
                  callback_data=f"adm:pay:{o['id']}")
    kb.button(text="Назад", callback_data="adm:home")
    kb.adjust(1)
    await c.message.edit_text(f"<b>Ожидают выплаты</b> ({len(rows)})", reply_markup=kb.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("adm:pay:"))
async def pay(c: CallbackQuery, cfg: Config, bot: Bot) -> None:
    if not await guard(c, cfg):
        return
    o = await db.pay_offer(int(c.data.split(":")[2]))
    if not o:
        return await c.answer("Уже выплачено", show_alert=True)
    await ui.send_safe(bot, o["recipient_id"],
                       f"Выплата по офферу #{o['id']} зачислена: {ui.amt(o['amount'], o['currency'])}.")
    await c.message.edit_text(f"Выплачено по офферу #{o['id']}.",
                              reply_markup=ui.KB(inline_keyboard=[[ui.B(text="К выплатам", callback_data="adm:payouts")]]))
    await c.answer("Готово")


@router.callback_query(F.data.startswith("adm:no:"))
async def admin_cancel(c: CallbackQuery, cfg: Config, bot: Bot) -> None:
    if not await guard(c, cfg):
        return
    o = await db.admin_cancel_offer(int(c.data.split(":")[2]))
    if not o:
        return await c.answer("Нельзя отменить", show_alert=True)
    await ui.send_safe(bot, o["recipient_id"], f"Сделка по офферу #{o['id']} отменена отправителем.")
    await ui.send_safe(bot, o["creator_id"],
                       f"Оффер #{o['id']} отменён, {ui.amt(o['amount'], o['currency'])} возвращены на баланс.")
    await c.message.edit_text("Отменено, средства возвращены.",
                              reply_markup=ui.KB(inline_keyboard=[[ui.B(text="Назад", callback_data="adm:home")]]))
    await c.answer("Готово")


# ---------- пользователи ----------

@router.callback_query(F.data.startswith("adm:users:"))
async def users(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    page = int(c.data.split(":")[2])
    rows = await db.search_users("", limit=1000)
    rows = rows[page * 10:(page + 1) * 10]
    kb = InlineKeyboardBuilder()
    for u in rows:
        kb.button(text=f"{u['id']} @{u['username'] or '—'}{' [бан]' if u['banned'] else ''}",
                  callback_data=f"adm:user:{u['id']}")
    if page > 0:
        kb.button(text="< Назад", callback_data=f"adm:users:{page - 1}")
    kb.button(text="Далее >", callback_data=f"adm:users:{page + 1}")
    kb.button(text="Админка", callback_data="adm:home")
    kb.adjust(1)
    await c.message.edit_text("<b>Пользователи</b>", reply_markup=kb.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def user_card(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    u = await db.get_user(int(c.data.split(":")[2]))
    if not u:
        return await c.answer("Не найден", show_alert=True)
    await c.message.edit_text(
        f"<b>Пользователь {u['id']}</b>\n\n"
        f"Имя: {ui.escape(u['full_name'])}\n"
        f"Username: {ui.uname(u['username'], '—')}\n"
        f"GRAM: {u['gram_balance']} · Stars: {u['stars_balance']}\n"
        f"Бан: {'да' if u['banned'] else 'нет'}",
        reply_markup=ui.KB(inline_keyboard=[
            [ui.B(text="+ GRAM", callback_data=f"adm:bal:{u['id']}:gram:+")],
            [ui.B(text="+ Stars", callback_data=f"adm:bal:{u['id']}:stars:+")],
            [ui.B(text="- GRAM", callback_data=f"adm:bal:{u['id']}:gram:-")],
            [ui.B(text="- Stars", callback_data=f"adm:bal:{u['id']}:stars:-")],
            [ui.B(text="Разбанить" if u["banned"] else "Забанить", callback_data=f"adm:ban:{u['id']}")],
            [ui.B(text="Удалить", callback_data=f"adm:del:{u['id']}")],
            [ui.B(text="Назад", callback_data="adm:users:0")],
        ]),
    )
    await c.answer()


@router.callback_query(F.data.startswith("adm:bal:"))
async def balance_start(c: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    _, _, uid, currency, sign = c.data.split(":")
    await state.set_state(Adm.balance)
    await state.update_data(uid=int(uid), currency=currency, sign=sign)
    await c.message.answer("Введите сумму.")
    await c.answer()


@router.message(Adm.balance)
async def balance_apply(m: Message, state: FSMContext, cfg: Config) -> None:
    if not is_admin(m.from_user.id, cfg):
        return
    if not (m.text or "").strip().isdigit():
        return await m.answer("Введите целое число.")
    d = await state.get_data()
    await state.clear()
    delta = int(m.text.strip()) * (1 if d["sign"] == "+" else -1)
    u = await db.change_balance(d["uid"], d["currency"], delta)
    await m.answer("Готово." if u else "Недостаточно средств.",
                   reply_markup=ui.KB(inline_keyboard=[[ui.B(text="К пользователю", callback_data=f"adm:user:{d['uid']}")]]))


@router.callback_query(F.data.startswith("adm:ban:"))
async def ban(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    uid = int(c.data.split(":")[2])
    u = await db.get_user(uid)
    if not u:
        return await c.answer("Не найден", show_alert=True)
    await db.set_banned(uid, not u["banned"])
    await c.answer("Забанен" if not u["banned"] else "Разбанен")
    await user_card(c, cfg)


@router.callback_query(F.data.startswith("adm:del:"))
async def delete(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    uid = int(c.data.split(":")[2])
    n = await db.delete_user(uid)
    await c.message.edit_text(f"Пользователь {uid} удалён, отменено офферов: {n}.",
                              reply_markup=ui.KB(inline_keyboard=[[ui.B(text="К списку", callback_data="adm:users:0")]]))
    await c.answer("Удалён")


@router.callback_query(F.data == "adm:find")
async def find(c: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    await state.set_state(Adm.find)
    await c.message.answer("Введите id или @username.")
    await c.answer()


@router.message(Adm.find)
async def find_apply(m: Message, state: FSMContext, cfg: Config) -> None:
    if not is_admin(m.from_user.id, cfg):
        return
    await state.clear()
    rows = await db.search_users((m.text or "").strip())
    if not rows:
        return await m.answer("Ничего не найдено.")
    kb = InlineKeyboardBuilder()
    for u in rows:
        kb.button(text=f"{u['id']} @{u['username'] or '—'}", callback_data=f"adm:user:{u['id']}")
    kb.adjust(1)
    await m.answer("Результаты:", reply_markup=kb.as_markup())


@router.callback_query(F.data == "adm:offers")
async def offers(c: CallbackQuery, cfg: Config) -> None:
    if not await guard(c, cfg):
        return
    rows = await db.list_offers(limit=20)
    kb = InlineKeyboardBuilder()
    for o in rows:
        kb.button(text=f"#{o['id']} {o['status']} {ui.amt(o['amount'], o['currency'])}",
                  callback_data=f"adm:off:{o['id']}")
    kb.button(text="Назад", callback_data="adm:home")
    kb.adjust(1)
    await c.message.edit_text("<b>Последние офферы</b>", reply_markup=kb.as_markup())
    await c.answer()
