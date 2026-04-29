"""
scraper.py — HTTP layer and BS4 fallback extraction.

LLM-driven extraction lives in analyzer.py. This module exposes:
  - fetch_html(url): raw HTML string (or None) with retry/backoff
  - extract_with_bs4(html, base_url, source_name): fallback heuristic
  - fetch_detail(url): cleaned text of a detail page (~3K chars)
"""
import logging
import re
import time
from datetime import date
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}
TIMEOUT = 15
MAX_RETRIES = 3
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
PER_SOURCE_CAP = 30


def fetch_html(url: str) -> Optional[str]:
    """GET with retry/backoff on 5xx and timeouts. Returns response.text or None."""
    last_err: Optional[str] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Fetch %s returned %d, retrying in %ds (attempt %d/%d)",
                    url, resp.status_code, wait, attempt + 1, MAX_RETRIES,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except requests.Timeout:
            last_err = "timeout"
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
        except requests.RequestException as e:
            last_err = str(e)
            break
    logger.warning("Failed to fetch %s: %s", url, last_err or "unknown")
    return None


def _normalize_url(url: str) -> str:
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


def extract_with_bs4(html: str, base_url: str, source_name: str) -> list[dict]:
    """
    Heuristic fallback: combine three extraction strategies (article > heading > li),
    dedup by URL, drop self-links to base_url.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    today = date.today().isoformat()
    base_norm = _normalize_url(base_url)

    def _from_articles() -> list[dict]:
        out = []
        for article in soup.find_all("article"):
            heading = article.find(["h1", "h2", "h3", "h4"])
            link_tag = article.find("a", href=True)
            if not (heading and link_tag):
                continue
            title = heading.get_text(strip=True)
            if not title:
                continue
            href = urljoin(base_url, link_tag["href"])
            p = article.find("p")
            description = p.get_text(strip=True) if p else ""
            out.append({
                "title": title,
                "url": href,
                "description": description[:500],
                "source_name": source_name,
                "date_found": today,
            })
        return out

    def _from_headings() -> list[dict]:
        out = []
        for heading in soup.find_all(["h2", "h3"]):
            link_tag = heading.find("a", href=True)
            if not link_tag:
                sibling = heading.find_next_sibling("a")
                if sibling and sibling.get("href"):
                    link_tag = sibling
            if not link_tag:
                continue
            title = heading.get_text(strip=True)
            if not title or len(title) < 10:
                continue
            href = urljoin(base_url, link_tag["href"])
            description = ""
            next_p = heading.find_next_sibling("p")
            if next_p:
                description = next_p.get_text(strip=True)[:500]
            out.append({
                "title": title,
                "url": href,
                "description": description,
                "source_name": source_name,
                "date_found": today,
            })
        return out

    def _from_list_items() -> list[dict]:
        out = []
        for li in soup.find_all("li"):
            link_tag = li.find("a", href=True)
            if not link_tag:
                continue
            title = link_tag.get_text(strip=True)
            if len(title) < 15:
                continue
            href = urljoin(base_url, link_tag["href"])
            out.append({
                "title": title,
                "url": href,
                "description": "",
                "source_name": source_name,
                "date_found": today,
            })
        return out

    seen_urls: set[str] = set()
    merged: list[dict] = []
    for strategy in (_from_articles, _from_headings, _from_list_items):
        for opp in strategy():
            if _normalize_url(opp["url"]) == base_norm:
                continue
            if opp["url"] in seen_urls:
                continue
            seen_urls.add(opp["url"])
            merged.append(opp)

    logger.info("BS4 extracted %d opportunities from %s", len(merged), source_name)
    return merged[:PER_SOURCE_CAP]


def fetch_detail(url: str) -> str:
    """Fetch a detail page and return up to 3000 chars of cleaned main content."""
    html = fetch_html(url)
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    main = soup.find(["article", "main"]) or soup.find("body")
    text = main.get_text(separator=" ", strip=True) if main else soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]
