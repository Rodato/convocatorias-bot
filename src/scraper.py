"""
scraper.py — Fetches and parses opportunities from each source URL.
Returns a list of {title, url, description, source_name, date_found} dicts.
"""
import logging
import re
from datetime import date
from typing import Optional
from urllib.parse import urljoin

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


def _fetch(url: str) -> Optional[BeautifulSoup]:
    """GET a URL and return a BeautifulSoup object, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except requests.RequestException as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return None


def _extract_opportunities(soup: BeautifulSoup, base_url: str, source_name: str) -> list[dict]:
    """
    Heuristic extraction: look for anchor tags near headings or inside
    article/li/div elements that resemble opportunity listings.
    Returns a deduplicated list of opportunity dicts.
    """
    today = date.today().isoformat()
    seen_urls: set[str] = set()
    results: list[dict] = []

    # Strategy 1: <article> elements with a link and heading
    for article in soup.find_all("article"):
        heading = article.find(["h1", "h2", "h3", "h4"])
        link_tag = article.find("a", href=True)
        if heading and link_tag:
            title = heading.get_text(strip=True)
            href = urljoin(base_url, link_tag["href"])
            if href in seen_urls or not title:
                continue
            seen_urls.add(href)
            # Description: first <p> in article, or empty string
            p = article.find("p")
            description = p.get_text(strip=True) if p else ""
            results.append({
                "title": title,
                "url": href,
                "description": description[:500],
                "source_name": source_name,
                "date_found": today,
            })

    # Strategy 2: headings (h2/h3) that contain or are followed by a link
    if not results:
        for heading in soup.find_all(["h2", "h3"]):
            # Link inside the heading
            link_tag = heading.find("a", href=True)
            if not link_tag:
                # Link as next sibling
                sibling = heading.find_next_sibling("a")
                if sibling and sibling.get("href"):
                    link_tag = sibling
            if not link_tag:
                continue
            title = heading.get_text(strip=True)
            href = urljoin(base_url, link_tag["href"])
            if href in seen_urls or not title or len(title) < 10:
                continue
            seen_urls.add(href)
            # Try to grab a nearby description
            description = ""
            next_p = heading.find_next_sibling("p")
            if next_p:
                description = next_p.get_text(strip=True)[:500]
            results.append({
                "title": title,
                "url": href,
                "description": description,
                "source_name": source_name,
                "date_found": today,
            })

    # Strategy 3: list items with links — common in simple portals
    if not results:
        for li in soup.find_all("li"):
            link_tag = li.find("a", href=True)
            if not link_tag:
                continue
            title = link_tag.get_text(strip=True)
            href = urljoin(base_url, link_tag["href"])
            if href in seen_urls or len(title) < 15:
                continue
            seen_urls.add(href)
            results.append({
                "title": title,
                "url": href,
                "description": "",
                "source_name": source_name,
                "date_found": today,
            })

    logger.info("Extracted %d opportunities from %s", len(results), source_name)
    return results[:30]  # cap per source to avoid noise


def fetch_detail(url: str) -> tuple[str, Optional[str]]:
    """
    Fetch a detail page and return (main_text, better_url).

    main_text: up to 3000 chars of the page's main content.
    better_url: a more specific application/opportunity link found on the page, or None.
    """
    soup = _fetch(url)
    if soup is None:
        return "", None

    # Try to find a more specific application link before stripping the DOM
    apply_keywords = [
        "apply", "aplicar", "postular", "application", "solicitud",
        "convocatoria", "register", "registrar", "submit", "inscripción",
    ]
    better_url = None
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True).lower()
        href = urljoin(url, a["href"])
        if href == url:
            continue
        if any(kw in link_text for kw in apply_keywords):
            better_url = href
            break

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    main = soup.find(["article", "main"]) or soup.find("body")
    text = main.get_text(separator=" ", strip=True) if main else soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000], better_url


def scrape_source(source: dict) -> list[dict]:
    """
    Public interface. Accepts a source dict with keys: name, url, type.
    Returns list of opportunity dicts.
    """
    logger.info("Scraping %s (%s)", source["name"], source["url"])
    soup = _fetch(source["url"])
    if soup is None:
        return []
    return _extract_opportunities(soup, source["url"], source["name"])
