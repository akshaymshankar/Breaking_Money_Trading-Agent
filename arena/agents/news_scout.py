"""News Scout: reads public news feeds (world, markets, the watchlist) and returns
clean, de-duplicated headlines, newest first.

Read-only, no keys. Every request passes the network allow-list in safety.py and
redirects are refused. Headlines are untrusted text from the internet: they are
stripped to plain characters, cut short, and only ever used as data.
"""
from __future__ import annotations

import html
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .. import compliance
from ..markets import MARKETS
from ..safety import SafeHTTP

log = logging.getLogger("arena")

# (source, scope, url)
WORLD = [
    ("BBC World", "world", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "world", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NPR World", "world", "https://feeds.npr.org/1004/rss.xml"),
    ("BBC Business", "business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
]
MARKET_FEEDS = {
    "US": [
        ("CNBC", "market", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC Markets", "market", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("MarketWatch", "market", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ],
    "IN": [
        ("Economic Times", "market", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
        ("Mint Markets", "market", "https://www.livemint.com/rss/markets"),
        ("CNBC", "market", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ],
}
YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={tickers}&region={region}&lang=en-US"
UA = "Mozilla/5.0 (compatible; paper-trading-arena)"
MAX_BYTES = 2_000_000
MAX_AGE_HOURS = 24


def clean(text: str, limit: int = 160) -> str:
    """Plain single-line text: no markup, no control or invisible characters."""
    text = html.unescape(re.sub(r"<[^>]*>", " ", html.unescape(text or "")))
    text = re.sub(r"<[^>]*>", " ", text)
    text = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c
                   for c in text if unicodedata.category(c) not in ("Cf", "Co", "Cs"))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_feed(xml: bytes, source: str, scope: str, now: datetime | None = None) -> list[dict]:
    if len(xml) > MAX_BYTES or b"<!ENTITY" in xml:
        raise ValueError("feed refused: too large or carries entity definitions")
    now = now or datetime.now(timezone.utc)
    out = []
    for item in ET.fromstring(xml).iter("item"):
        title = clean(item.findtext("title") or "")
        if len(title) < 12:
            continue
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate") or "")
            ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue  # undated items cannot be placed in time
        age = (now - ts).total_seconds() / 60
        if age > MAX_AGE_HOURS * 60 or age < -15:
            continue
        link = (item.findtext("link") or "").strip()
        out.append({"title": title, "source": source, "scope": scope, "age_min": max(0, round(age)),
                    "link": link if re.match(r"https?://", link) else ""})
    return out


def tag_symbols(title: str, names: dict[str, str]) -> list[str]:
    """Which watchlist stocks a headline is about. Short tickers need an exact-case match."""
    hits = []
    for sym, name in names.items():
        if re.search(rf"(?<![A-Za-z]){re.escape(sym)}(?![A-Za-z])", title) and (len(sym) > 2 or f"({sym})" in title):
            hits.append(sym)
        elif len(name) > 3 and name.upper() != sym and re.search(rf"\b{re.escape(name)}\b", title, re.I):
            hits.append(sym)
    return hits


def _short_names(symbols: list[str]) -> dict[str, str]:
    from ..marketdata import NAMES
    drop = r"\b(Platforms|Industries|Consultancy|Motors|Holdings|Ltd|Limited|Inc|ETF)\b"
    return {s: re.sub(drop, "", NAMES.get(s, "")).strip() for s in symbols}


def collect(region: str, symbols: list[str], http: SafeHTTP | None = None, limit: int = 60) -> dict:
    """{"items": [...], "sources_ok": n, "sources_failed": [...]}"""
    mk = MARKETS[region]
    http = http or SafeHTTP(timeout=8)
    feeds = WORLD + MARKET_FEEDS.get(region, [])
    symbols = compliance.allowed(symbols, mk)
    if symbols:
        tickers = ",".join(re.sub(r"[^A-Z0-9.^&-]", "", s.upper()) + mk.yahoo_suffix for s in symbols[:12]).replace("&", "%26")
        feeds = feeds + [("Yahoo Finance", "stock", YAHOO.format(tickers=tickers, region="IN" if region == "IN" else "US"))]

    def fetch(feed):
        source, scope, url = feed
        r = http.get(url, headers={"User-Agent": UA}, allow_redirects=False)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return parse_feed(r.content, source, scope)

    items, failed = [], []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [(f, ex.submit(fetch, f)) for f in feeds]
    for f, fut in futures:
        try:
            items += fut.result()
        except Exception as e:
            failed.append(f[0])
            log.info("News feed %s skipped: %s", f[0], str(e)[:120])
    names = _short_names(symbols)
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: x["age_min"]):
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        it["symbols"] = tag_symbols(it["title"], names)
        uniq.append(it)
    # Keep a mix: no single scope may crowd out the others.
    cap = {"world": limit // 3, "business": limit // 6, "market": limit // 3, "stock": limit // 3}
    kept, used = [], {}
    for it in uniq:
        used[it["scope"]] = used.get(it["scope"], 0) + 1
        if used[it["scope"]] <= cap.get(it["scope"], limit // 3):
            kept.append(it)
    return {"items": kept[:limit], "sources_ok": len(feeds) - len(failed), "sources_failed": failed}
