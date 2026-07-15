"""Search aggregators for DMHY / ACG.RIP / Nyaa (RSS + DMHY HTML)."""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from .config import HTTP_TIMEOUT, SOURCES, USER_AGENT

# DMHY posts in Asia/Shanghai
_TZ_CST = timezone(timedelta(hours=8))


def _parse_pubdate(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def _parse_dmhy_datetime(text: str) -> Optional[str]:
    """Parse DMHY list date like '2025/12/29 15:13' (CST)."""
    if not text:
        return None
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    try:
        dt = datetime(y, mo, d, h, mi, tzinfo=_TZ_CST)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _parse_size_bytes(size_str: Optional[str]) -> Optional[int]:
    if not size_str:
        return None
    s = str(size_str).strip().replace(",", "").replace(" ", "")
    if s in ("-", "—", ""):
        return None
    if s.isdigit():
        return int(s)
    # Accept: 3.1GB, 591.8MB, 1.4 GiB, 528.1MB, 659MB
    m = re.match(r"^([\d.]+)\s*([KMGTPE])(I?B)?$", s, re.I)
    if not m:
        m = re.match(r"^([\d.]+)\s*(B)$", s, re.I)
        if not m:
            return None
        return int(float(m.group(1)))
    num = float(m.group(1))
    prefix = m.group(2).upper()
    rest = (m.group(3) or "B").upper()
    # DMHY uses decimal-ish labels (MB/GB); treat as 1024-based for consistency with BT
    mult_map = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5, "E": 1024**6}
    return int(num * mult_map.get(prefix, 1))


def _parse_count(text: Optional[str]) -> Optional[int]:
    """Parse seeder/leecher/complete cells; '-' -> None."""
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if not s or s in ("-", "—", "N/A", "n/a"):
        return None
    m = re.search(r"\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _format_size(n: Optional[int]) -> str:
    if n is None or n <= 0:
        return "-"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(n)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{size:.1f} {u}" if u != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


def _clean_title(title: str) -> str:
    return html.unescape((title or "").strip())


def _magnet_from_hash(info_hash: str, title: str = "") -> str:
    h = info_hash.strip().lower()
    dn = quote(title) if title else ""
    trackers = [
        "http://nyaa.tracker.wf:7777/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "http://open.acgtracker.com:1096/announce",
        "http://t.nyaatracker.com:80/announce",
    ]
    parts = [f"magnet:?xt=urn:btih:{h}"]
    if dn:
        parts.append(f"dn={dn}")
    for t in trackers:
        parts.append(f"tr={quote(t, safe='')}")
    return "&".join(parts)


async def _fetch_bytes(url: str, accept: str = "*/*") -> bytes:
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _fetch_rss(url: str) -> feedparser.FeedParserDict:
    content = await _fetch_bytes(
        url, accept="application/rss+xml, application/xml, text/xml, */*"
    )
    return feedparser.parse(content)


def _entry_common(entry: Any, source: str) -> Dict[str, Any]:
    title = _clean_title(getattr(entry, "title", "") or "")
    link = getattr(entry, "link", "") or getattr(entry, "id", "") or ""
    pub = _parse_pubdate(getattr(entry, "published", None) or getattr(entry, "updated", None))
    author = ""
    if hasattr(entry, "author"):
        author = entry.author or ""
    category = ""
    if hasattr(entry, "tags") and entry.tags:
        category = entry.tags[0].get("term", "") if isinstance(entry.tags[0], dict) else str(entry.tags[0])
    elif hasattr(entry, "category"):
        category = entry.category or ""
    return {
        "id": f"{source}:{link or title}",
        "source": source,
        "source_name": SOURCES[source]["name"],
        "title": title,
        "page_url": link,
        "published": pub,
        "author": author,
        "category": category,
        "magnet": None,
        "torrent_url": None,
        "size": None,
        "size_text": "-",
        "seeders": None,
        "leechers": None,
        "completed": None,
    }


def _parse_dmhy_html(content: bytes, base: str = "https://share.dmhy.org") -> List[Dict[str, Any]]:
    """
    Parse DMHY list HTML (#topic_list).
    Columns: 日期 | 分类 | 标题 | 磁链 | 大小 | 种子 | 下载 | 完成 | 发布人
    RSS has no size/seeders — must use HTML for those fields.
    """
    soup = BeautifulSoup(content, "lxml")
    table = soup.select_one("#topic_list") or soup.select_one("table.tablesorter")
    if not table:
        return []

    items: List[Dict[str, Any]] = []
    for row in table.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 9:
            continue

        # date
        date_text = tds[0].get_text(" ", strip=True)
        published = _parse_dmhy_datetime(date_text)

        # category
        category = tds[1].get_text(strip=True)

        # title + page url
        title_td = tds[2]
        title_a = title_td.select_one("a[href*='/topics/view/']") or title_td.find("a", href=True)
        title = _clean_title(title_a.get_text(strip=True) if title_a else title_td.get_text(" ", strip=True))
        # strip trailing comment noise like "約5條評論"
        title = re.sub(r"\s*約\d+條評論\s*$", "", title)
        page_url = ""
        if title_a and title_a.get("href"):
            page_url = urljoin(base, title_a["href"])

        # magnet
        magnet = None
        torrent_url = None
        for a in tds[3].find_all("a", href=True):
            href = html.unescape(a["href"].strip())
            if href.startswith("magnet:"):
                magnet = href
            elif href.endswith(".torrent") or "/torrent/" in href:
                torrent_url = urljoin(base, href)

        # size / seeders / leechers / completed
        size_text = tds[4].get_text(strip=True) or "-"
        seeders = _parse_count(tds[5].get_text(strip=True))
        leechers = _parse_count(tds[6].get_text(strip=True))
        completed = _parse_count(tds[7].get_text(strip=True))
        author = tds[8].get_text(strip=True)

        size_bytes = _parse_size_bytes(size_text)

        items.append(
            {
                "id": f"dmhy:{page_url or title}",
                "source": "dmhy",
                "source_name": SOURCES["dmhy"]["name"],
                "title": title,
                "page_url": page_url,
                "published": published,
                "author": author,
                "category": category,
                "magnet": magnet,
                "torrent_url": torrent_url,
                "size": size_bytes,
                "size_text": size_text if size_text else "-",
                "seeders": seeders,
                "leechers": leechers,
                "completed": completed,
            }
        )
    return items


async def search_dmhy(keyword: str = "") -> List[Dict[str, Any]]:
    """DMHY via HTML list so size / seed / leecher / complete are available."""
    kw = keyword.strip()
    if kw:
        url = f"https://share.dmhy.org/topics/list?{urlencode({'keyword': kw})}"
    else:
        url = "https://share.dmhy.org/topics/list"
    content = await _fetch_bytes(url, accept="text/html,application/xhtml+xml,*/*")
    return _parse_dmhy_html(content)


def _parse_acgrip(feed: feedparser.FeedParserDict) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for entry in feed.entries:
        item = _entry_common(entry, "acgrip")
        enclosures = getattr(entry, "enclosures", None) or []
        for enc in enclosures:
            href = enc.get("href") or enc.get("url") or ""
            if href:
                item["torrent_url"] = href
        # contentLength via torrent namespace / media
        cl = None
        if hasattr(entry, "torrent_contentlength"):
            try:
                cl = int(entry.torrent_contentlength)
            except Exception:
                pass
        if cl is None and hasattr(entry, "media_content") and entry.media_content:
            try:
                cl = int(entry.media_content[0].get("filesize") or 0) or None
            except Exception:
                pass
        item["size"] = cl
        item["size_text"] = _format_size(cl)
        items.append(item)
    return items


def _parse_nyaa(feed: feedparser.FeedParserDict) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for entry in feed.entries:
        item = _entry_common(entry, "nyaa")
        info_hash = getattr(entry, "nyaa_infohash", None) or ""
        size_text = getattr(entry, "nyaa_size", None) or ""
        seeders = getattr(entry, "nyaa_seeders", None)
        leechers = getattr(entry, "nyaa_leechers", None)
        category = getattr(entry, "nyaa_category", None) or item["category"]
        item["category"] = category
        item["size"] = _parse_size_bytes(size_text)
        item["size_text"] = size_text or _format_size(item["size"])
        try:
            item["seeders"] = int(seeders) if seeders is not None else None
        except Exception:
            item["seeders"] = None
        try:
            item["leechers"] = int(leechers) if leechers is not None else None
        except Exception:
            item["leechers"] = None
        if info_hash:
            item["magnet"] = _magnet_from_hash(info_hash, item["title"])
        # torrent download link
        link = getattr(entry, "link", "") or ""
        if "download" in link and link.endswith(".torrent"):
            item["torrent_url"] = link
        elif item.get("page_url"):
            # convert view -> download
            m = re.search(r"/view/(\d+)", item["page_url"])
            if m:
                item["torrent_url"] = f"https://nyaa.si/download/{m.group(1)}.torrent"
        items.append(item)
    return items


PARSERS = {
    "acgrip": _parse_acgrip,
    "nyaa": _parse_nyaa,
}


def _build_search_url(source: str, keyword: str) -> str:
    cfg = SOURCES[source]
    kw = keyword.strip()
    if source == "acgrip":
        if not kw:
            return cfg["latest_rss"]
        return f"{cfg['search_rss']}?{urlencode({'term': kw})}"
    if source == "nyaa":
        # anime category 1_0
        if not kw:
            return cfg["latest_rss"]
        return f"https://nyaa.si/?{urlencode({'page': 'rss', 'q': kw, 'c': '1_0', 'f': '0'})}"
    raise ValueError(f"unknown source: {source}")


async def search_source(source: str, keyword: str = "") -> Dict[str, Any]:
    """Search a single source. Returns {source, items, error?}."""
    t0 = time.time()
    try:
        if source == "dmhy":
            items = await search_dmhy(keyword)
        else:
            url = _build_search_url(source, keyword)
            feed = await _fetch_rss(url)
            items = PARSERS[source](feed)
        return {
            "source": source,
            "source_name": SOURCES[source]["name"],
            "items": items,
            "count": len(items),
            "elapsed_ms": int((time.time() - t0) * 1000),
            "error": None,
        }
    except Exception as e:
        return {
            "source": source,
            "source_name": SOURCES[source]["name"],
            "items": [],
            "count": 0,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "error": str(e),
        }


async def search_all(
    keyword: str = "",
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Parallel search across selected sources; merge & sort by published desc."""
    import asyncio

    selected = sources or list(SOURCES.keys())
    selected = [s for s in selected if s in SOURCES]
    results = await asyncio.gather(*[search_source(s, keyword) for s in selected])

    merged: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    for r in results:
        if r["error"]:
            errors[r["source"]] = r["error"]
        merged.extend(r["items"])

    def sort_key(it: Dict[str, Any]):
        pub = it.get("published") or ""
        return pub

    merged.sort(key=sort_key, reverse=True)
    return {
        "keyword": keyword,
        "items": merged,
        "total": len(merged),
        "sources": [
            {
                "source": r["source"],
                "source_name": r["source_name"],
                "count": r["count"],
                "elapsed_ms": r["elapsed_ms"],
                "error": r["error"],
            }
            for r in results
        ],
        "errors": errors,
    }
