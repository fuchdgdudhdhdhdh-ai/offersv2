from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any, Callable

log = logging.getLogger("db")

_conn: sqlite3.Connection | None = None
_lock = asyncio.Lock()

ACTIVE = ("pending", "accepted")
OPEN = ("pending", "accepted", "completed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY,
    username            TEXT,
    full_name           TEXT,
    gram_balance        INTEGER NOT NULL DEFAULT 0,
    stars_balance       INTEGER NOT NULL DEFAULT 0,
    banned              INTEGER NOT NULL DEFAULT 0,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS offers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id          INTEGER NOT NULL,
    creator_username    TEXT,
    recipient_id        INTEGER NOT NULL,
    recipient_username  TEXT,
    gift_link           TEXT NOT NULL,
    gift_slug           TEXT NOT NULL,
    gift_title          TEXT NOT NULL,
    amount              INTEGER NOT NULL,
    currency            TEXT NOT NULL CHECK (currency IN ('gram','stars')),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','accepted','completed','paid','rejected','cancelled')),
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    charge_id   TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offers_creator ON offers(creator_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_offers_recipient ON offers(recipient_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status, id DESC);
"""

MIGRATIONS = (
    "ALTER TABLE offers ADD COLUMN recipient_claimed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE offers ADD COLUMN completed_at INTEGER",
    "ALTER TABLE offers ADD COLUMN paid_at INTEGER",
)


def aio(fn: Callable) -> Callable:
    async def wrapper(*args, **kwargs):
        async with _lock:
            return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _col(currency: str) -> str:
    return "gram_balance" if currency == "gram" else "stars_balance"


def connect(path: str) -> None:
    global _conn
    _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            _conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    log.info("БД открыта: %s", path)


def close() -> None:
    if _conn:
        _conn.close()
        log.info("БД закрыта")


# ---------- пользователи ----------

@aio
def upsert_user(user_id: int, username: str | None, full_name: str) -> dict:
    now = int(time.time())
    _conn.execute(
        "INSERT INTO users(id, username, full_name, created_at, updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
        "full_name=excluded.full_name, updated_at=excluded.updated_at",
        (user_id, username, full_name, now, now),
    )
    return _row(_conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


@aio
def get_user(user_id: int) -> dict | None:
    return _row(_conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


@aio
def get_user_by_username(username: str) -> dict | None:
    return _row(_conn.execute(
        "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.lstrip("@"),)
    ).fetchone())


@aio
def change_balance(user_id: int, currency: str, delta: int) -> dict | None:
    col = _col(currency)
    _conn.execute("BEGIN IMMEDIATE")
    try:
        u = _conn.execute(f"SELECT {col} FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            _conn.execute("ROLLBACK")
            return None
        new = u[0] + delta
        if new < 0:
            _conn.execute("ROLLBACK")
            return None
        _conn.execute(f"UPDATE users SET {col}=?, updated_at=? WHERE id=?", (new, int(time.time()), user_id))
        _conn.execute("COMMIT")
    except Exception:
        _conn.execute("ROLLBACK")
        raise
    log.info("баланс user=%s %s %+d -> %d", user_id, currency, delta, new)
    return get_user_sync(user_id)


def get_user_sync(user_id: int) -> dict | None:
    return _row(_conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


@aio
def set_banned(user_id: int, banned: bool) -> None:
    _conn.execute("UPDATE users SET banned=?, updated_at=? WHERE id=?", (int(banned), int(time.time()), user_id))


@aio
def search_users(query: str, limit: int = 10) -> list[dict]:
    if query.lstrip("-").isdigit():
        rows = _conn.execute("SELECT * FROM users WHERE id=? LIMIT ?", (int(query), limit))
    else:
        rows = _conn.execute(
            "SELECT * FROM users WHERE username LIKE ? OR full_name LIKE ? LIMIT ?",
            (f"%{query.lstrip('@')}%", f"%{query}%", limit),
        )
    return [dict(r) for r in rows]


@aio
def delete_user(user_id: int) -> int:
    refunded = 0
    for o in [dict(r) for r in _conn.execute(
        "SELECT * FROM offers WHERE status IN (?,?,?) AND (creator_id=? OR recipient_id=?)",
        (*OPEN, user_id, user_id),
    )]:
        if o["creator_id"] == user_id:
            _conn.execute(
                f"UPDATE users SET {_col(o['currency'])}={_col(o['currency'])}+? WHERE id=?",
                (o["amount"], o["creator_id"]),
            )
            refunded += 1
        _conn.execute("UPDATE offers SET status='cancelled', updated_at=? WHERE id=?", (int(time.time()), o["id"]))
    _conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    log.info("удалён user=%s, отменено офферов=%d", user_id, refunded)
    return refunded


# ---------- офферы ----------

@aio
def create_offer(creator: dict, recipient: dict, gift: dict, amount: int, currency: str) -> dict | None:
    col = _col(currency)
    now = int(time.time())
    _conn.execute("BEGIN IMMEDIATE")
    try:
        u = _conn.execute(f"SELECT {col} FROM users WHERE id=?", (creator["id"],)).fetchone()
        if not u or u[0] < amount:
            _conn.execute("ROLLBACK")
            return None
        _conn.execute(f"UPDATE users SET {col}={col}-?, updated_at=? WHERE id=?", (amount, now, creator["id"]))
        cur = _conn.execute(
            "INSERT INTO offers(creator_id, creator_username, recipient_id, recipient_username, "
            "gift_link, gift_slug, gift_title, amount, currency, status, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)",
            (creator["id"], creator["username"], recipient["id"], recipient["username"],
             gift["link"], gift["slug"], gift["title"], amount, currency, now, now),
        )
        _conn.execute("COMMIT")
    except Exception:
        _conn.execute("ROLLBACK")
        raise
    log.info("создан оффер #%s %s -> %s %d %s", cur.lastrowid, creator["id"], recipient["id"], amount, currency)
    return _row(_conn.execute("SELECT * FROM offers WHERE id=?", (cur.lastrowid,)).fetchone())


@aio
def get_offer(offer_id: int) -> dict | None:
    return _row(_conn.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone())


@aio
def list_offers(creator_id: int | None = None, recipient_id: int | None = None,
                status: str | None = None, limit: int = 10) -> list[dict]:
    q, args, cond = "SELECT * FROM offers", [], []
    if creator_id is not None:
        cond.append("creator_id=?")
        args.append(creator_id)
    if recipient_id is not None:
        cond.append("recipient_id=?")
        args.append(recipient_id)
    if status:
        cond.append("status=?")
        args.append(status)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in _conn.execute(q, args)]


def _transition(offer_id: int, allowed: tuple[str, ...], new: str) -> dict | None:
    now = int(time.time())
    _conn.execute("BEGIN IMMEDIATE")
    try:
        o = _conn.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
        if not o or o["status"] not in allowed:
            _conn.execute("ROLLBACK")
            return None
        col = _col(o["currency"])
        extra = ""
        if new in ("rejected", "cancelled"):
            _conn.execute(f"UPDATE users SET {col}={col}+?, updated_at=? WHERE id=?", (o["amount"], now, o["creator_id"]))
        elif new == "paid":
            _conn.execute(f"UPDATE users SET {col}={col}+?, updated_at=? WHERE id=?", (o["amount"], now, o["recipient_id"]))
            extra = f", paid_at={now}"
        elif new == "completed":
            extra = f", completed_at={now}"
        _conn.execute(f"UPDATE offers SET status=?, updated_at=?{extra} WHERE id=?", (new, now, offer_id))
        _conn.execute("COMMIT")
    except Exception:
        _conn.execute("ROLLBACK")
        raise
    log.info("оффер #%s: %s -> %s", offer_id, o["status"], new)
    return _row(_conn.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone())


@aio
def accept_offer(offer_id: int) -> dict | None:
    return _transition(offer_id, ("pending",), "accepted")


@aio
def reject_offer(offer_id: int) -> dict | None:
    return _transition(offer_id, ("pending",), "rejected")


@aio
def cancel_offer(offer_id: int) -> dict | None:
    return _transition(offer_id, ("pending",), "cancelled")


@aio
def admin_cancel_offer(offer_id: int) -> dict | None:
    return _transition(offer_id, OPEN, "cancelled")


@aio
def complete_offer(offer_id: int) -> dict | None:
    return _transition(offer_id, ("accepted",), "completed")


@aio
def pay_offer(offer_id: int) -> dict | None:
    return _transition(offer_id, ("completed",), "paid")


@aio
def set_claimed(offer_id: int) -> None:
    _conn.execute("UPDATE offers SET recipient_claimed=1 WHERE id=?", (offer_id,))


# ---------- платежи ----------

@aio
def record_payment(charge_id: str, user_id: int, amount: int) -> bool:
    cur = _conn.execute(
        "INSERT OR IGNORE INTO payments(charge_id, user_id, amount, created_at) VALUES(?,?,?,?)",
        (charge_id, user_id, amount, int(time.time())),
    )
    return cur.rowcount == 1


# ---------- статистика ----------

@aio
def stats() -> dict:
    q = _conn.execute
    return {
        "users": q("SELECT COUNT(*) FROM users").fetchone()[0],
        "banned": q("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0],
        "offers": q("SELECT COUNT(*) FROM offers").fetchone()[0],
        "pending": q("SELECT COUNT(*) FROM offers WHERE status='pending'").fetchone()[0],
        "queue_confirm": q("SELECT COUNT(*) FROM offers WHERE status='accepted'").fetchone()[0],
        "queue_payout": q("SELECT COUNT(*) FROM offers WHERE status='completed'").fetchone()[0],
        "paid_sum": q("SELECT COALESCE(SUM(amount),0) FROM offers WHERE status='paid'").fetchone()[0],
        "stars_total": q("SELECT COALESCE(SUM(stars_balance),0) FROM users").fetchone()[0],
        "gram_total": q("SELECT COALESCE(SUM(gram_balance),0) FROM users").fetchone()[0],
    }