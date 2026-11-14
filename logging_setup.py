from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-14s | %(message)s"


def setup(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        os.makedirs("logs", exist_ok=True)
        file = RotatingFileHandler("logs/bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        file.setFormatter(fmt)
        root.addHandler(file)
    except OSError:
        logging.getLogger("logging").warning("файловый лог недоступен, пишу только в stdout")

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)