"""
notifier.py — Sends relevant opportunities to Slack via Incoming Webhook.
"""
import logging
import os
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _format_deadline(opp: dict) -> str:
    """Prefer parsed deadline_iso (YYYY-MM-DD with days-left). Fallback to original string."""
    deadline_iso: Optional[str] = opp.get("deadline_iso")
    deadline_raw: Optional[str] = opp.get("deadline")

    if deadline_iso == "rolling":
        return "Convocatoria permanente (sin fecha límite fija)"

    if deadline_iso:
        try:
            d = datetime.strptime(deadline_iso, "%Y-%m-%d").date()
            days_left = (d - date.today()).days
            if days_left < 0:
                suffix = "VENCIDA"
            elif days_left == 0:
                suffix = "vence hoy"
            elif days_left == 1:
                suffix = "vence mañana"
            else:
                suffix = f"en {days_left} días"
            return f"{deadline_iso} ({suffix})"
        except (ValueError, TypeError):
            pass

    return deadline_raw or "No especificada"


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

    lines.append(f"📅 *Fecha límite:* {_format_deadline(opp)}")

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


# Slack hard cap is 50 blocks per message. Each opp = 1 divider + 1 section = 2 blocks,
# plus a header. 23 opps → 1 + 23*2 = 47 blocks fits comfortably.
OPPS_PER_MESSAGE = 23


def send_to_slack(opportunities: list[dict]) -> None:
    """
    POST opportunities to Slack via webhook. If the list is empty,
    sends a "no new opportunities today" summary.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.error("SLACK_WEBHOOK_URL not set, skipping Slack notification")
        return

    if not opportunities:
        _post(webhook_url, {
            "text": "📭 *Resumen diario — Convocatorias*\nSin nuevas convocatorias relevantes hoy."
        })
        return

    total = len(opportunities)
    for start in range(0, total, OPPS_PER_MESSAGE):
        chunk = opportunities[start : start + OPPS_PER_MESSAGE]
        if total <= OPPS_PER_MESSAGE:
            header_text = f"🗓️ Convocatorias del día — {total} nueva(s)"
        else:
            header_text = f"🗓️ Convocatorias ({start + 1}–{start + len(chunk)} de {total})"

        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": header_text}}
        ]
        for opp in chunk:
            blocks.append(_divider())
            blocks.append(_build_block(opp))
        _post(webhook_url, {"blocks": blocks})


def _post(webhook_url: str, payload: dict) -> None:
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Slack notification sent successfully")
    except requests.RequestException as e:
        logger.error("Failed to send Slack notification: %s", e)
