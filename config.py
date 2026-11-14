from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int_set(raw: str) -> frozenset[int]:
    out = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return frozenset(out)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    webhook_base: str
    webhook_secret: str
    port: int
    db_path: str
    gifts_to: str
    webapp_url: str
    log_level: str
    stars_pack: int
    stars_price: int
    min_offer: int


def load() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if ":" not in token:
        raise SystemExit("BOT_TOKEN не задан или некорректен")

    admins = _int_set(os.getenv("ADMIN_IDS", ""))
    if not admins:
        raise SystemExit("ADMIN_IDS пуст — админку никто не увидит")

    return Config(
        bot_token=token,
        admin_ids=admins,
        webhook_base=os.getenv("WEBHOOK_BASE_URL", "").rstrip("/"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip() or "devsecret",
        port=int(os.getenv("PORT", "8080")),
        db_path=os.getenv("DB_PATH", "bot.db"),
        gifts_to=os.getenv("GIFTS_TO", "").strip().lstrip("@"),
        webapp_url=os.getenv("WEBAPP_URL", "").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        stars_pack=int(os.getenv("STARS_PACK", "100")),
        stars_price=int(os.getenv("STARS_PRICE", "150")),
        min_offer=int(os.getenv("MIN_OFFER", "1")),
    )