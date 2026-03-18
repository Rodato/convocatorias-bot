"""
analyzer.py — Uses OpenRouter API to filter opportunities by relevance to Estudio Plural.
"""
import json
import logging
import os
import sys

from openai import OpenAI

# Allow running from repo root or from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import KEYWORDS, ORG_PROFILE

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
MODEL = "anthropic/claude-sonnet-4.6"

SYSTEM_PROMPT = f"""Eres un asistente especializado en identificar oportunidades de financiamiento y convocatorias relevantes para organizaciones de consultoría social en Colombia.

Perfil de la organización:
{ORG_PROFILE}

Palabras clave de interés:
{", ".join(KEYWORDS)}

Tu tarea: dada una lista de oportunidades (título + descripción + URL), identifica cuáles son relevantes para esta organización. Una oportunidad es relevante si:
1. Se relaciona con alguna de las áreas de trabajo de la organización, O
2. Contiene alguna de las palabras clave, O
3. Es una convocatoria para consultoría o servicios en temas sociales, culturales, de género, comunicación o similares en Colombia o América Latina.

Responde ÚNICAMENTE con un JSON válido — un array de objetos con esta estructura exacta:
[
  {{
    "url": "url de la oportunidad",
    "title": "título de la oportunidad",
    "reason": "razón en 1 línea de por qué es relevante para Estudio Plural"
  }}
]

Si ninguna oportunidad es relevante, responde con un array vacío: []
No incluyas texto adicional fuera del JSON.
"""


def filter_relevant(opportunities: list[dict]) -> list[dict]:
    """
    Sends opportunities to Claude in batches and returns only the relevant ones,
    each enriched with a 'reason' field.
    """
    if not opportunities:
        return []

    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    relevant: list[dict] = []

    for i in range(0, len(opportunities), BATCH_SIZE):
        batch = opportunities[i : i + BATCH_SIZE]
        batch_payload = [
            {
                "title": opp["title"],
                "description": opp.get("description", ""),
                "url": opp["url"],
                "source": opp.get("source_name", ""),
            }
            for opp in batch
        ]

        logger.info(
            "Sending batch %d-%d to OpenRouter (%d opportunities)",
            i + 1, i + len(batch), len(batch)
        )

        try:
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Analiza estas oportunidades y devuelve solo las relevantes:\n\n{json.dumps(batch_payload, ensure_ascii=False, indent=2)}",
                    },
                ],
            )
            raw = response.choices[0].message.content.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            filtered = json.loads(raw)
            if not isinstance(filtered, list):
                logger.warning("Unexpected response format, skipping batch")
                continue

            # Enrich with original opportunity metadata
            url_to_opp = {opp["url"]: opp for opp in batch}
            for item in filtered:
                url = item.get("url", "")
                original = url_to_opp.get(url, {})
                enriched = {**original, "reason": item.get("reason", "")}
                relevant.append(enriched)

        except json.JSONDecodeError as e:
            logger.error("Failed to parse response as JSON: %s", e)
        except Exception as e:
            logger.error("OpenRouter API error: %s", e)

    logger.info("Claude identified %d relevant opportunities out of %d", len(relevant), len(opportunities))
    return relevant
