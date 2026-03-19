"""
analyzer.py — Uses OpenRouter API to filter opportunities by relevance to Estudio Plural.
"""
import json
import logging
import os
import sys
from datetime import date

from openai import OpenAI

# Allow running from repo root or from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import KEYWORDS, ORG_PROFILE

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
MODEL = "anthropic/claude-sonnet-4.6"
TODAY = date.today().isoformat()  # evaluated once at startup (daily cron — safe)

SYSTEM_PROMPT = f"""Eres un asistente especializado en identificar oportunidades de financiamiento y convocatorias relevantes para organizaciones de consultoría social en Colombia.

Fecha de hoy: {TODAY}. Si una oportunidad menciona una fecha límite que ya pasó o que vence en menos de 7 días, NO la incluyas en los resultados.

Perfil de la organización:
{ORG_PROFILE}

Palabras clave de interés:
{", ".join(KEYWORDS)}

Tu tarea: dado un listado de oportunidades (título + descripción corta + URL), determina cuáles vale la pena revisar en detalle para esta organización.

IMPORTANTE: las descripciones son cortas y frecuentemente incompletas — ante la duda, INCLUYE la oportunidad. Es mejor revisar de más que perder una convocatoria relevante. Solo excluye las que claramente no tienen ninguna relación con las áreas de trabajo o palabras clave.

Incluye una oportunidad si:
1. El título o descripción sugiere relación con alguna de las áreas de trabajo, O
2. Contiene o sugiere alguna de las palabras clave, O
3. Parece ser una convocatoria de consultoría, financiamiento o servicios en temas sociales, culturales, de género, comunicación, medio ambiente o similares en Colombia o América Latina, O
4. No hay suficiente información para decidir (beneficio de la duda).

Responde ÚNICAMENTE con un JSON válido — un array de objetos con esta estructura exacta:
[
  {{
    "url": "url de la oportunidad",
    "title": "título de la oportunidad",
    "reason": "razón en 1 línea de por qué podría ser relevante para Estudio Plural"
  }}
]

Si ninguna oportunidad aplica, responde con un array vacío: []
No incluyas texto adicional fuera del JSON.
"""


ENRICH_PROMPT = f"""Dado el texto completo de una convocatoria, extrae los siguientes campos. Si un campo no se menciona explícitamente, usa null.

Fecha de hoy: {TODAY}. Si el deadline ya pasó o vence en menos de 7 días, indícalo añadiendo " (VENCIDA)" al final de la fecha en el campo deadline.

Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{{
  "is_open_call": true,
  "deadline": "fecha límite de aplicación o null",
  "funding_amount": "monto o rango de financiación (con moneda) o null",
  "eligibility": "requisitos administrativos y de elegibilidad resumidos en 1-2 líneas, o null",
  "experience_years": "años de experiencia requeridos o null",
  "themes": ["tema 1", "tema 2"]
}}

El campo `is_open_call` debe ser:
- true: si la página describe una convocatoria específica y activa con proceso de aplicación (formulario, instrucciones de envío, deadline, etc.)
- false: si es una página general de programa, descripción de cómo financia una organización, o catálogo sin llamado abierto actualmente

No incluyas texto adicional fuera del JSON."""


def enrich_details(opportunities: list[dict]) -> list[dict]:
    """
    For each opportunity, uses its detail_text to extract structured fields via Claude.
    Falls back to the original opp dict if the call fails.
    """
    if not opportunities:
        return []

    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    enriched: list[dict] = []

    for opp in opportunities:
        detail_text = opp.get("detail_text", "")
        if not detail_text:
            enriched.append(opp)
            continue

        try:
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": ENRICH_PROMPT},
                    {
                        "role": "user",
                        "content": f"Título: {opp.get('title', '')}\n\nTexto:\n{detail_text}",
                    },
                ],
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            fields = json.loads(raw)
            enriched.append({**opp, **fields})
        except Exception as e:
            logger.error("Failed to enrich %s: %s", opp.get("url"), e)
            enriched.append(opp)

    logger.info("Enriched %d opportunities with detail text", len(enriched))
    return enriched


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
                        "content": f"Filtra estas oportunidades y devuelve las que vale la pena revisar:\n\n{json.dumps(batch_payload, ensure_ascii=False, indent=2)}",
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
