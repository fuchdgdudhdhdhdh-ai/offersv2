from __future__ import annotations

import logging
import secrets

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
import gift
import ui
from config import Config

log = logging.getLogger("user")
router = Router(name="user")

PAYLOAD_TOP = "top"


class Create(StatesGroup):
    recipient = State()
    link = State()
    amount = State()
    currency = State()
    confirm = State()


class TopUp(StatesGroup):
    stars = State()


def make_payload(kind: str, user_id: int, amount: int) -> str:
    return f"{kind}:{user_id}:{amount}:{secrets.token_hex(4)}"


def parse_payload(raw: str) -> dict | None:
    parts = (raw or "").split(":")
    if len(parts) != 4:
        return None
    kind, uid, amount, nonce = parts
    if not kind or not uid.isdigit() or not amount.isdigit():
        return None
    return {"kind": kind, "user_id": int(uid), "amount": int(amount), "nonce": nonce}


async def show_menu(target: Message | CallbackQuery, user: dict, edit: bool = False) -> None:
    text, kb = ui.menu_text(user), ui.main_menu(user)
    if edit and isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            log.debug("edit не удался, шлём новое сообщение", exc_info=True)
    msg = target.message if isinstance(target, CallbackQuery) else target
    await msg.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, user: dict) -> None:
    await state.clear()
    log.info("/start user=%s", m.from_user.id)
    await m.answer(ui.START, reply_markup=ui.main_menu(user))


@router.message(Command("help"))
async def help_cmd(m: Message) -> None:
    await m.answer(ui.HELP, reply_markup=ui.back_menu())


@router.message(Command("cancel"))
async def cancel_cmd(m: Message, state: FSMContext, user: dict) -> None:
    await state.clear()
    await m.answer("Отменено.", reply_markup=ui.main_menu(user))


@router.callback_query(F.data == "menu")
async def menu_cb(c: CallbackQuery, state: FSMContext, user: dict) -> None:
    await state.clear()
    await show_menu(c, user, edit=True)
    await c.answer()


@router.callback_query(F.data == "help")
async def help_cb(c: CallbackQuery) -> None:
    await c.message.answer(ui.HELP, reply_markup=ui.back_menu())
    await c.answer()


# ---------- пополнение ----------

@router.callback_query(F.data == "top")
async def top(c: CallbackQuery) -> None:
    await c.message.answer(
        "<b>Пополнение баланса</b>\n\n"
        "Stars зачисляются автоматически. GRAM зачисляет оператор вручную после проверки перевода.",
        reply_markup=ui.topup_menu(),
    )
    await c.answer()


@router.callback_query(F.data == "top:gram")
async def top_gram(c: CallbackQuery, cfg: Config) -> None:
    await c.message.answer(
        "<b>Пополнение GRAM</b>\n\n"
        f"Переведите сумму на @{cfg.gifts_to or 'оператора'} и пришлите чек.\n"
        "Зачисление в течение 1-24 часов.",
        reply_markup=ui.back_menu(),
    )
    await c.answer()


@router.callback_query(F.data == "top:stars")
async def top_stars(c: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    await state.set_state(TopUp.stars)
    await c.message.answer(
        f"Сколько Stars пополнить? Пакет {cfg.stars_pack} стоит {cfg.stars_price} ⭐.\n"
        f"Введите число от 1 до {cfg.stars_pack} или отправьте /cancel.",
        reply_markup=ui.back_menu(),
    )
    await c.answer()


@router.message(TopUp.stars)
async def top_stars_amount(m: Message, state: FSMContext, cfg: Config, user: dict) -> None:
    raw = (m.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= cfg.stars_pack:
        await m.answer(f"Введите число от 1 до {cfg.stars_pack}.")
        return

    amount = int(raw)
    await state.clear()
    await m.answer_invoice(
        title=f"Пополнение на {amount} ⭐",
        description=f"Зачисление {amount} Stars на баланс бота.",
        payload=make_payload(PAYLOAD_TOP, user["id"], amount),
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, cfg: Config) -> None:
    p = parse_payload(q.invoice_payload)

    if not p:
        log.warning("pre_checkout: битый payload %r от %s", q.invoice_payload, q.from_user.id)
        return await q.answer(ok=False, error_message="Неизвестный счёт. Оформите заново.")

    if p["kind"] != PAYLOAD_TOP:
        log.warning("pre_checkout: чужой тип счёта %r", p["kind"])
        return await q.answer(ok=False, error_message="Этот товар больше не продаётся.")

    if q.from_user.id != p["user_id"]:
        log.warning("pre_checkout: счёт user=%s, платит user=%s", p["user_id"], q.from_user.id)
        return await q.answer(ok=False, error_message="Счёт оформлен на другого пользователя.")

    if q.currency != "XTR":
        return await q.answer(ok=False, error_message="Неверная валюта счёта.")

    if q.total_amount != p["amount"]:
        log.warning("pre_checkout: сумма payload=%s, факт=%s", p["amount"], q.total_amount)
        return await q.answer(ok=False, error_message="Сумма счёта изменилась, оформите заново.")

    if not 1 <= p["amount"] <= cfg.stars_pack:
        return await q.answer(ok=False, error_message="Недопустимая сумма.")

    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(m: Message, user: dict) -> None:
    p = m.successful_payment
    parsed = parse_payload(p.invoice_payload)

    if not parsed or parsed["kind"] != PAYLOAD_TOP:
        log.error("оплата без распознанного payload %r, charge=%s",
                  p.invoice_payload, p.telegram_payment_charge_id)
        await m.answer("Платёж получен, но счёт не распознан. Напишите администратору.")
        return

    if parsed["user_id"] != user["id"]:
        log.error("оплата не тому: payload=%s, плательщик=%s", parsed["user_id"], user["id"])

    amount = parsed["amount"]
    if p.total_amount != amount:
        log.error("расхождение суммы: payload=%d, факт=%d", amount, p.total_amount)
        amount = p.total_amount

    if not await db.record_payment(p.telegram_payment_charge_id, user["id"], amount):
        log.warning("дубль платежа %s", p.telegram_payment_charge_id)
        return await m.answer("Этот платёж уже зачислен.")

    fresh = await db.change_balance(user["id"], "stars", amount)
    log.info("оплата user=%s +%d stars, charge=%s", user["id"], amount, p.telegram_payment_charge_id)
    await m.answer(f"Баланс пополнен на {amount} ⭐.")
    await show_menu(m, fresh or user)


# ---------- создание оффера ----------

@router.callback_query(F.data == "of:new")
async def new_offer(c: CallbackQuery, state: FSMContext, user: dict) -> None:
    if user["gram_balance"] <= 0 and user["stars_balance"] <= 0:
        await c.answer("Сначала пополните баланс", show_alert=True)
        return await top(c)

    await state.set_state(Create.recipient)
    await c.message.answer(
        "Введите @username получателя (он должен быть зарегистрирован в боте).",
        reply_markup=ui.cancel_kb(),
    )
    await c.answer()


@router.callback_query(F.data == "of:new_cancel")
async def new_cancel(c: CallbackQuery, state: FSMContext, user: dict) -> None:
    await state.clear()
    await show_menu(c, user, edit=True)
    await c.answer()


@router.message(Create.recipient)
async def set_recipient(m: Message, state: FSMContext, user: dict) -> None:
    raw = (m.text or "").strip().lstrip("@")
    if not raw:
        await m.answer("Введите @username.")
        return

    target = await db.get_user_by_username(raw)
    if not target:
        await m.answer(
            f"Пользователь @{ui.escape(raw)} не найден — он должен сначала нажать /start в боте.",
            reply_markup=ui.cancel_kb(),
        )
        return
    if target["id"] == user["id"]:
        await m.answer("Это вы. Введите другого получателя.")
        return

    await state.update_data(recipient={"id": target["id"], "username": target["username"]})
    await state.set_state(Create.link)
    await m.answer(
        f"Получатель: {ui.uname(target['username'], target['id'])}\n\n"
        "Пришлите ссылку на подарок: <code>https://t.me/nft/&lt;slug&gt;</code>",
        reply_markup=ui.cancel_kb(),
    )


@router.message(Create.link)
async def set_link(m: Message, state: FSMContext) -> None:
    data = await gift.resolve(m.text or "")
    if not data:
        await m.answer(
            "Не похоже на ссылку. Пример: https://t.me/nft/plushpepe-1234",
            reply_markup=ui.cancel_kb(),
        )
        return

    await state.update_data(gift=data)
    await state.set_state(Create.amount)
    await m.answer(
        f"Подарок: <b>{ui.escape(data['title'])}</b>\n\nВведите сумму.",
        reply_markup=ui.cancel_kb(),
    )


@router.message(Create.amount)
async def set_amount(m: Message, state: FSMContext, user: dict) -> None:
    raw = (m.text or "").strip().replace(" ", "")
    if not raw.isdigit() or int(raw) < 1:
        await m.answer("Введите целое число больше нуля.")
        return

    amount = int(raw)
    if user["gram_balance"] < amount and user["stars_balance"] < amount:
        await m.answer(
            f"Недостаточно средств: GRAM {user['gram_balance']}, Stars {user['stars_balance']}."
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(Create.currency)

    kb = InlineKeyboardBuilder()
    if user["gram_balance"] >= amount:
        kb.button(text=f"GRAM ({user['gram_balance']})", callback_data="cur:gram")
    if user["stars_balance"] >= amount:
        kb.button(text=f"Stars ({user['stars_balance']})", callback_data="cur:stars")
    kb.button(text="Отмена", callback_data="of:new_cancel")
    kb.adjust(1)
    await m.answer("Выберите валюту.", reply_markup=kb.as_markup())


@router.callback_query(Create.currency, F.data.startswith("cur:"))
async def set_currency(c: CallbackQuery, state: FSMContext, user: dict) -> None:
    currency = c.data.split(":")[1]
    data = await state.get_data()

    if user[f"{currency}_balance"] < data["amount"]:
        await c.answer("Недостаточно средств", show_alert=True)
        return

    await state.update_data(currency=currency)
    await state.set_state(Create.confirm)
    await c.message.answer(
        "<b>Проверьте оффер</b>\n\n"
        f"Получатель: {ui.uname(data['recipient']['username'], data['recipient']['id'])}\n"
        f"Подарок: {ui.escape(data['gift']['title'])}\n"
        f"Ссылка: {data['gift']['link']}\n"
        f"Сумма: <b>{ui.amt(data['amount'], currency)}</b>\n\n"
        "Сумма замораживается на балансе до завершения сделки.",
        reply_markup=ui.confirm_kb(),
    )
    await c.answer()


@router.callback_query(Create.confirm, F.data == "of:create")
async def create(c: CallbackQuery, state: FSMContext, user: dict, bot: Bot, cfg: Config) -> None:
    data = await state.get_data()
    offer = await db.create_offer(user, data["recipient"], data["gift"], data["amount"], data["currency"])
    await state.clear()

    if not offer:
        await c.message.answer("Не хватило средств. Пополните баланс.", reply_markup=ui.back_menu())
        await c.answer()
        return

    widget_message = await ui.send_widget(bot, offer["recipient_id"], offer)
    if widget_message and widget_message.get("message_id"):
        await db.set_widget_message(offer["id"], offer["recipient_id"], widget_message["message_id"])
        sent = True
    else:
        reason = (widget_message or {}).get("error", "неизвестная ошибка")
        log.error("HTML-виджет оффера #%s не отправлен: %s", offer["id"], reason)
        await db.cancel_offer(offer["id"])
        await c.message.answer(
            f"Не удалось отправить HTML-виджет получателю. Оффер #{offer['id']} отменён, средства возвращены.\n"
            f"Причина: <code>{ui.escape(str(reason))}</code>",
            reply_markup=ui.back_menu(),
        )
        await c.answer()
        return

    await c.message.answer(
        f"Оффер #{offer['id']} отправлен. {ui.amt(offer['amount'], offer['currency'])} заморожены.",
        reply_markup=ui.back_menu(),
    )
    await c.answer()


# ---------- действия по офферу ----------

@router.callback_query(F.data.startswith("of:acc:"))
async def accept(c: CallbackQuery, user: dict, bot: Bot, cfg: Config) -> None:
    o = await db.get_offer(int(c.data.split(":")[2]))
    if not o:
        return await c.answer("Оффер не найден", show_alert=True)
    if o["recipient_id"] != user["id"]:
        return await c.answer("Оффер не ваш", show_alert=True)
    if o["status"] != "pending":
        return await c.answer("Оффер уже не в этом статусе", show_alert=True)

    new = await db.accept_offer(o["id"])
    if not new:
        return await c.answer("Уже обработано", show_alert=True)

    await c.message.edit_text(
        ui.offer_text(new, "recipient"),
        reply_markup=ui.offer_kb(new, "recipient", cfg.webapp_url, cfg.gifts_to),
    )
    await ui.send_safe(
        bot, new["creator_id"],
        f"{ui.uname(new['recipient_username'], new['recipient_id'])} принял оффер #{new['id']} "
        f"на {ui.amt(new['amount'], new['currency'])}.\n"
        "Ждём передачи подарка, подтвердить получение можно в /admin.",
        ui.offer_kb(new, "creator", cfg.webapp_url, cfg.gifts_to),
    )
    await c.answer("Принято")


@router.callback_query(F.data.startswith("of:rej:"))
async def reject(c: CallbackQuery, user: dict, bot: Bot) -> None:
    o = await db.get_offer(int(c.data.split(":")[2]))
    if not o or o["recipient_id"] != user["id"]:
        return await c.answer("Оффер не ваш", show_alert=True)

    new = await db.reject_offer(o["id"])
    if not new:
        return await c.answer("Уже обработано", show_alert=True)

    await c.message.edit_text(ui.offer_text(new, "recipient"), reply_markup=ui.offer_kb(new, "recipient"))
    await ui.send_safe(
        bot, new["creator_id"],
        f"{ui.uname(new['recipient_username'], new['recipient_id'])} отклонил оффер #{new['id']}. "
        f"{ui.amt(new['amount'], new['currency'])} возвращены на баланс.",
    )
    await c.answer("Отклонено")


@router.callback_query(F.data.startswith("of:cancel:"))
async def cancel(c: CallbackQuery, user: dict, bot: Bot) -> None:
    o = await db.get_offer(int(c.data.split(":")[2]))
    if not o or o["creator_id"] != user["id"]:
        return await c.answer("Оффер не ваш", show_alert=True)
    if o["status"] != "pending":
        return await c.answer("После принятия отменяет администратор", show_alert=True)

    new = await db.cancel_offer(o["id"])
    if not new:
        return await c.answer("Уже обработано", show_alert=True)

    await ui.send_safe(bot, new["recipient_id"], f"Оффер #{new['id']} отменён отправителем.")
    await ui.send_safe(bot, new["creator_id"], f"Оффер #{new['id']} отменён, средства возвращены.")
    await c.answer("Отменено")


@router.callback_query(F.data == "of:list")
async def my_offers(c: CallbackQuery, user: dict) -> None:
    rows = await db.list_offers(creator_id=user["id"], limit=10)
    if not rows:
        return await c.answer("Офферов нет", show_alert=True)

    kb = InlineKeyboardBuilder()
    for o in rows:
        kb.button(text=f"#{o['id']} · {ui.STATUS_LABEL[o['status']]}", callback_data=f"of:show:{o['id']}")
    kb.button(text="Назад", callback_data="menu")
    kb.adjust(1)
    await c.message.answer("<b>Мои офферы</b>", reply_markup=kb.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("of:show:"))
async def show_offer(c: CallbackQuery, user: dict, cfg: Config) -> None:
    o = await db.get_offer(int(c.data.split(":")[2]))
    if not o or user["id"] not in (o["creator_id"], o["recipient_id"]):
        return await c.answer("Нет доступа", show_alert=True)

    role = "creator" if o["creator_id"] == user["id"] else "recipient"
    await c.message.answer(
        ui.offer_text(o, role),
        reply_markup=ui.offer_kb(o, role, cfg.webapp_url, cfg.gifts_to),
        link_preview_options=ui.NO_PREVIEW,
    )
    await c.answer()
# ---------- Telegram HTML Widget actions ----------
async def handle_widget_query(query: dict, bot: Bot, cfg: Config) -> None:
    query_id = str(query.get("id") or "")
    if not query_id:
        return
    try:
        sender = query.get("from") or {}
        chat = query.get("chat") or {}
        user_id = int(sender.get("id") or 0)
        chat_id = int(chat.get("id") or 0)
        message_id = int(query.get("message_id") or 0)
        action = str(query.get("data") or "")
        if chat.get("type") != "private" or user_id <= 0 or chat_id != user_id or message_id <= 0:
            await ui.answer_widget_query(bot, query_id, "Действие доступно только получателю в личном чате с ботом.")
            return
        offer = await db.get_offer_by_widget(chat_id, message_id)
        if not offer or offer["recipient_id"] != user_id:
            await ui.answer_widget_query(bot, query_id, "Оффер не найден или у вас нет доступа.")
            return
        if action == "offer:accept":
            new = await db.accept_offer(offer["id"])
            if not new:
                await ui.answer_widget_query(bot, query_id, "Этот оффер уже обработан.")
                return
            await ui.edit_widget_data(bot, chat_id, message_id, new)
            await ui.answer_widget_query(bot, query_id, "Оффер принят.")
            await ui.send_safe(
                bot, new["creator_id"],
                f"{ui.uname(new['recipient_username'], new['recipient_id'])} принял оффер #{new['id']} "
                f"на {ui.amt(new['amount'], new['currency'])}. Ожидается передача NFT.",
                ui.offer_kb(new, "creator", cfg.webapp_url, cfg.gifts_to),
            )
            return
        if action == "offer:reject":
            new = await db.reject_offer(offer["id"])
            if not new:
                await ui.answer_widget_query(bot, query_id, "Этот оффер уже обработан.")
                return
            await ui.edit_widget_data(bot, chat_id, message_id, new)
            await ui.answer_widget_query(bot, query_id, "Оффер отклонён. Средства возвращены отправителю.")
            await ui.send_safe(
                bot, new["creator_id"],
                f"{ui.uname(new['recipient_username'], new['recipient_id'])} отклонил оффер #{new['id']}. "
                f"{ui.amt(new['amount'], new['currency'])} возвращены на баланс.",
            )
            return
        await ui.answer_widget_query(bot, query_id, "Неизвестное действие.")
    except Exception:
        log.exception("ошибка обработки widget_query id=%s", query_id)
        await ui.answer_widget_query(bot, query_id, "Внутренняя ошибка. Попробуйте ещё раз.")
