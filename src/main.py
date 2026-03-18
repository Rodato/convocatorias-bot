"""
main.py — Orchestrator for the Convocatorias Bot.

Flow:
1. Load sources from data/sources.json
2. Scrape each source for opportunities
3. Filter out already-seen URLs (data/seen.json)
4. Send new opportunities through Claude to filter by relevance
5. Notify Slack with relevant ones
6. Update seen.json with all newly scraped URLs
"""
import json
import logging
import os
import sys
from pathlib import Path

# Allow running from repo root or from src/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.analyzer import filter_relevant
from src.notifier import send_to_slack
from src.scraper import scrape_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

SOURCES_FILE = ROOT / "data" / "sources.json"
SEEN_FILE = ROOT / "data" / "seen.json"


def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Could not parse seen.json, starting fresh")
    return set()


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2, ensure_ascii=False))


def main() -> None:
    logger.info("=== Convocatorias Bot starting ===")

    # 1. Load sources
    sources = json.loads(SOURCES_FILE.read_text())
    logger.info("Loaded %d sources", len(sources))

    # 2. Scrape all sources
    all_opportunities: list[dict] = []
    for source in sources:
        opportunities = scrape_source(source)
        all_opportunities.extend(opportunities)

    logger.info("Total scraped: %d opportunities", len(all_opportunities))

    # 3. Filter out already-seen URLs
    seen = load_seen()
    new_opportunities = [opp for opp in all_opportunities if opp["url"] not in seen]
    logger.info("New (unseen) opportunities: %d", len(new_opportunities))

    # 4. Run Claude relevance filter
    relevant = []
    if new_opportunities:
        relevant = filter_relevant(new_opportunities)
    logger.info("Relevant after Claude filter: %d", len(relevant))

    # 5. Notify Slack
    send_to_slack(relevant)

    # 6. Update seen.json with ALL scraped URLs (not just relevant ones)
    seen.update(opp["url"] for opp in all_opportunities)
    save_seen(seen)
    logger.info("Saved %d total seen URLs", len(seen))

    logger.info("=== Convocatorias Bot finished ===")


if __name__ == "__main__":
    main()
