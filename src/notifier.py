"""
notifier.py — Sends relevant opportunities to Slack via Incoming Webhook.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)


def _build_block(opp: dict) -> dict:
    """Build a Slack Block Kit section for one opportunity."""
    title = opp.get("title", "Sin título")
    source = opp.get("source_name", "Fuente desconocida")
    reason = opp.get("reason", "")
    url = opp.get("url", "")

    lines = [
        f"*{title}*",
        f"*Fuente:* {source}",
        f"*Relevancia:* {reason}",
    ]

    deadline = opp.get("deadline")
    if deadline:
        lines.append(f"📅 *Deadline:* {deadline}")

    funding = opp.get("funding_amount")
    if funding:
        lines.append(f"💰 *Monto:* {funding}")

    eligibility = opp.get("eligibility")
    if eligibility:
        lines.append(f"📋 *Elegibilidad:* {eligibility}")

    experience = opp.get("experience_years")
    if experience:
        lines.append(f"🏅 *Experiencia requerida:* {experience}")

    themes = opp.get("themes")
    if themes and isinstance(themes, list) and len(themes) > 0:
        lines.append(f"🏷️ *Temáticas:* {', '.join(themes)}")

    lines.append(f"🔗 <{url}|Ver convocatoria completa>")

    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}


def _divider() -> dict:
    return {"type": "divider"}


def send_to_slack(opportunities: list[dict]) -> None:
    """
    POST a Slack message for each opportunity.
    If the list is empty, sends a daily summary saying no new opportunities were found.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.error("SLACK_WEBHOOK_URL not set, skipping Slack notification")
        return

    if not opportunities:
        payload = {
            "text": "📭 *Resumen diario — Convocatorias*\nSin nuevas convocatorias relevantes hoy."
        }
        _post(webhook_url, payload)
        return

    # Send in one message with blocks (up to 50 blocks; split if needed)
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🗓️ Convocatorias del día — {len(opportunities)} nueva(s)",
            },
        }
    ]
    for opp in opportunities:
        blocks.append(_divider())
        blocks.append(_build_block(opp))

    # Slack allows max 50 blocks per message; send in chunks if needed
    MAX_BLOCKS = 48  # leave room for header + trailing divider
    if len(blocks) <= MAX_BLOCKS:
        _post(webhook_url, {"blocks": blocks})
    else:
        # Split into multiple messages
        chunk_size = MAX_BLOCKS - 1  # -1 for header
        for i in range(0, len(opportunities), chunk_size // 2):
            chunk = opportunities[i : i + chunk_size // 2]
            chunk_blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🗓️ Convocatorias ({i + 1}–{i + len(chunk)} de {len(opportunities)})",
                    },
                }
            ]
            for opp in chunk:
                chunk_blocks.append(_divider())
                chunk_blocks.append(_build_block(opp))
            _post(webhook_url, {"blocks": chunk_blocks})


def _post(webhook_url: str, payload: dict) -> None:
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Slack notification sent successfully")
    except requests.RequestException as e:
        logger.error("Failed to send Slack notification: %s", e)
