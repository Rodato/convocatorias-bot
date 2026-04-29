"""
main.py — Orchestrator for the Convocatorias Bot.

Flow:
1. Load sources from data/sources.json
2. For each source: fetch HTML → LLM extract (Haiku) → fallback BS4 if 0
3. Filter out URLs already in MongoDB
4. Run filter_relevant (Haiku) on the new ones
5. Fetch detail pages, enrich (Sonnet) → page_type + structured fields
6. Split single_call vs listing_page
7. Drill-in (1 level): for each listing_page, re-extract → filter → enrich → keep singles
8. Notify Slack with the merged single_calls
9. Save all new URLs to MongoDB
"""
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.analyzer import (
    enrich_details,
    extract_opportunities_llm,
    filter_relevant,
    split_after_enrichment,
)
from src.notifier import send_to_slack
from src.scraper import PER_SOURCE_CAP, fetch_detail, fetch_html
from src.storage import filter_seen, save_new_opportunities

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

SOURCES_FILE = ROOT / "data" / "sources.json"
SCRAPE_WORKERS = 8
DETAIL_WORKERS = 6


def _scrape_one(source: dict) -> list[dict]:
    """
    Extract candidates from a source.
    - LLM extraction is the primary path.
    - If LLM finds items, return them (it's a listing of multiple calls).
    - If LLM finds 0 items, treat the source URL itself as a single candidate so
      enrichment can classify it (single_call, listing_page, or general_info).
      This handles single-call sources (e.g. CEPF) and edge cases where the LLM
      is conservative.
    """
    name = source["name"]
    url = source["url"]
    logger.info("Scraping %s (%s)", name, url)
    html = fetch_html(url)
    if not html:
        return []

    items = extract_opportunities_llm(html, url, name)
    if items:
        return items[:PER_SOURCE_CAP]

    logger.info("LLM returned 0 items from %s, queuing source URL as candidate", name)
    return [{
        "title": name,
        "url": url,
        "description": "",
        "source_name": name,
        "date_found": date.today().isoformat(),
    }]


def _scrape_all(sources: list[dict]) -> list[dict]:
    all_opps: list[dict] = []
    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as pool:
        for opps in pool.map(_scrape_one, sources):
            all_opps.extend(opps)
    return all_opps


def _populate_detail_text(opportunities: list[dict]) -> None:
    def _populate(opp: dict) -> None:
        opp["detail_text"] = fetch_detail(opp["url"])

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        list(pool.map(_populate, opportunities))


def _drill_in(
    listings: list[dict],
    seen_urls: set[str],
    already_queued: set[str],
) -> list[dict]:
    """
    For each listing_page, re-extract individual opportunities via LLM,
    then run filter_relevant + enrich + final classification.
    Returns the new single_calls discovered. Cap recursion = 1 (no further drill-in).
    """
    drilled: list[dict] = []
    for listing in listings:
        url = listing["url"]
        html = fetch_html(url)
        if not html:
            continue
        sub_source_name = f"{listing.get('source_name', '')} → {listing.get('title', '')[:60]}"
        sub_items = extract_opportunities_llm(html, url, sub_source_name)
        if not sub_items:
            continue
        # Dedup vs already-seen URLs (DB) and items already in this run's queue
        new_subs = [
            opp for opp in sub_items
            if opp["url"] not in seen_urls and opp["url"] not in already_queued
        ]
        for opp in new_subs:
            already_queued.add(opp["url"])
        if new_subs:
            drilled.extend(new_subs)
            logger.info(
                "Drill-in from %s extracted %d new sub-items",
                listing.get("source_name", ""), len(new_subs),
            )

    if not drilled:
        return []

    # Run drilled items through the same filter+enrich pipeline (no further drilling)
    drilled_relevant = filter_relevant(drilled)
    if not drilled_relevant:
        return []
    _populate_detail_text(drilled_relevant)
    drilled_relevant = enrich_details(drilled_relevant)
    single_calls, _listings = split_after_enrichment(drilled_relevant)
    return single_calls


def main() -> None:
    logger.info("=== Convocatorias Bot starting ===")

    sources = json.loads(SOURCES_FILE.read_text())
    logger.info("Loaded %d sources", len(sources))

    all_opportunities = _scrape_all(sources)
    logger.info("Total scraped: %d opportunities", len(all_opportunities))

    scraped_urls = {opp["url"] for opp in all_opportunities}
    seen = filter_seen(scraped_urls)
    new_opportunities = [opp for opp in all_opportunities if opp["url"] not in seen]
    logger.info("New (unseen) opportunities: %d", len(new_opportunities))

    relevant: list[dict] = []
    if new_opportunities:
        relevant = filter_relevant(new_opportunities)

    single_calls: list[dict] = []
    if relevant:
        logger.info("Fetching detail pages for %d relevant opportunities...", len(relevant))
        _populate_detail_text(relevant)
        relevant = enrich_details(relevant)
        single_calls, listings = split_after_enrichment(relevant)

        if listings:
            already_queued = {opp["url"] for opp in new_opportunities}
            drilled_singles = _drill_in(listings, seen, already_queued)
            if drilled_singles:
                logger.info("Drill-in produced %d additional single_calls", len(drilled_singles))
                single_calls.extend(drilled_singles)
                # Add drilled URLs to new_opportunities so they get persisted in MongoDB
                drilled_urls_existing = {opp["url"] for opp in new_opportunities}
                for opp in drilled_singles:
                    if opp["url"] not in drilled_urls_existing:
                        new_opportunities.append(opp)

    send_to_slack(single_calls)
    save_new_opportunities(new_opportunities, single_calls)

    logger.info("=== Convocatorias Bot finished ===")


if __name__ == "__main__":
    main()
