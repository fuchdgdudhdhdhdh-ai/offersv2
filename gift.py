from __future__ import annotations

import logging
import re

import aiohttp

log = logging.getLogger("gift")

_SLUG = re.compile(r"(?:t\.me/nft/|tg://nft\?slug=)?([A-Za-z0-9_-]{2,64})")
_TITLE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
_cache: dict[str, str] = {}

PRETTY = {
    "plushpepe": "Plush Pepe",
    "heart": "Heart",
    "bear": "Bear",
    "diamond": "Diamond",
}


def parse(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    m = _SLUG.search(raw)
    if not m:
        return None
    slug = m.group(1)
    if slug in ("nft", "https", "http", "t.me", "tg"):
        return None
    return slug


def pretty(slug: str) -> str:
    if slug in PRETTY:
        return PRETTY[slug]
    base = re.sub(r"[_-]?\d+$", "", slug).replace("_", " ").replace("-", " ")
    base = base.strip() or slug
    return base[:1].upper() + base[1:]


async def fetch_title(slug: str) -> str:
    if slug in _cache:
        return _cache[slug]
    url = f"https://t.me/nft/{slug}"
    title = ""
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status == 200:
                    html = await r.text(errors="ignore")
                    m = _TITLE.search(html)
                    if m:
                        title = m.group(1).strip()
    except Exception:
        log.warning("не удалось получить название для %s", slug, exc_info=True)

    if not title or title.lower().startswith("telegram"):
        title = pretty(slug)
    title = title[:64]
    _cache[slug] = title
    return title


async def resolve(raw: str) -> dict | None:
    slug = parse(raw)
    if not slug:
        return None
    return {"slug": slug, "link": f"https://t.me/nft/{slug}", "title": await fetch_title(slug)}