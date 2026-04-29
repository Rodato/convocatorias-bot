"""
storage.py — MongoDB persistence for the Convocatorias Bot.

Each scraped opportunity is stored as a document. Deduplication via a unique
index on `url`. We only write previously-unseen URLs to avoid clobbering
enriched fields on subsequent runs.

Schema (per document):
  url             str   — unique, used for dedup
  title           str
  description     str
  source_name     str
  date_found      str   — ISO date (YYYY-MM-DD)
  is_relevant     bool  — set by Claude filter
  sent_to_slack   bool
  # Enriched fields (only present for relevant opportunities):
  reason          str
  deadline        str | null
  deadline_iso    str | null   — YYYY-MM-DD parsed by Claude
  funding_amount  str | null
  eligibility     str | null
  experience_years str | null
  themes          list[str] | null
  page_type       str           — single_call | listing_page | general_info
"""
import logging
import os
from typing import Optional

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import ConnectionFailure

logger = logging.getLogger(__name__)

_client: Optional[MongoClient] = None
_collection: Optional[Collection] = None


def _get_collection() -> Collection:
    global _client, _collection
    if _collection is not None:
        return _collection

    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI environment variable is not set")

    _client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    try:
        _client.admin.command("ping")
    except ConnectionFailure as e:
        raise RuntimeError(f"Could not connect to MongoDB: {e}") from e

    db = _client["convocatorias_bot"]
    _collection = db["opportunities"]
    _collection.create_index("url", unique=True)
    logger.info("Connected to MongoDB — collection: %s", _collection.full_name)
    return _collection


def filter_seen(urls: set[str]) -> set[str]:
    """Given a set of scraped URLs, return the subset already present in MongoDB."""
    if not urls:
        return set()
    col = _get_collection()
    cursor = col.find({"url": {"$in": list(urls)}}, {"url": 1, "_id": 0})
    seen = {doc["url"] for doc in cursor}
    logger.info("DB lookup: %d of %d scraped URLs already seen", len(seen), len(urls))
    return seen


def save_new_opportunities(
    new_opportunities: list[dict],
    relevant: list[dict],
) -> None:
    """
    Insert previously-unseen opportunities into MongoDB.

    All opportunities here are guaranteed new (already filtered by `filter_seen`).
    Relevant ones receive enriched fields + sent_to_slack=True.
    """
    if not new_opportunities:
        logger.info("No new opportunities to save")
        return

    col = _get_collection()
    relevant_by_url = {opp["url"]: opp for opp in relevant}

    ops: list[UpdateOne] = []
    for opp in new_opportunities:
        url = opp["url"]
        is_relevant = url in relevant_by_url

        doc: dict = {
            "url": url,
            "title": opp.get("title", ""),
            "description": opp.get("description", ""),
            "source_name": opp.get("source_name", ""),
            "date_found": opp.get("date_found", ""),
            "is_relevant": is_relevant,
            "sent_to_slack": is_relevant,
        }

        if is_relevant:
            enriched = relevant_by_url[url]
            doc.update({
                "reason": enriched.get("reason"),
                "deadline": enriched.get("deadline"),
                "deadline_iso": enriched.get("deadline_iso"),
                "funding_amount": enriched.get("funding_amount"),
                "eligibility": enriched.get("eligibility"),
                "experience_years": enriched.get("experience_years"),
                "themes": enriched.get("themes"),
                "page_type": enriched.get("page_type", "single_call"),
            })

        ops.append(UpdateOne({"url": url}, {"$set": doc}, upsert=True))

    result = col.bulk_write(ops, ordered=False)
    logger.info(
        "MongoDB write — upserted: %d, modified: %d",
        result.upserted_count,
        result.modified_count,
    )
