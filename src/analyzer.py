"""
analyzer.py — OpenRouter-backed extraction, filtering, and enrichment.

Three LLM-driven steps:
  1. extract_opportunities_llm: HTML → individual {title, url, brief}
  2. filter_relevant: candidates → relevant subset (Haiku)
  3. enrich_details: detail page → structured fields incl. page_type (Sonnet)
"""
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, NavigableString
from openai import OpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import KEYWORDS, ORG_PROFILE

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
FILTER_MODEL = "anthropic/claude-haiku-4.5"
EXTRACT_MODEL = "anthropic/claude-haiku-4.5"
ENRICH_MODEL = "anthropic/claude-sonnet-4.6"
ENRICH_WORKERS = 6
TODAY = date.today().isoformat()
DEADLINE_GRACE_DAYS = 7
HTML_MAX_CHARS = 20000

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
        )
    return _client


# ---------- Prompts ----------

EXTRACT_PROMPT = """Eres un asistente que extrae convocatorias / llamados a propuestas / licitaciones individuales de páginas web.

Te llega: la URL de la página, su nombre fuente, y el contenido limpio (con links como [texto](url)).

Reglas:
1. Devuelve SOLO llamados específicos a postularse a algo (consultorías, licitaciones, becas, fondos, RFPs, llamados a propuestas, términos de referencia, tenders, grants, ofertas de servicios).

2. EXCLUYE: navegación/menús/footer, redes sociales, paginación, "Convocatorias cerradas", premios pasados, páginas institucionales ("Nuestra historia", "Misión", "Quiénes somos"), blog posts, eventos pasados, formularios de contacto, links a la home, links a páginas de "cómo aplicar" genéricas.

3. Si la página es un LISTING (lista varias convocatorias), devuelve cada una con su URL específica. NO incluyas la URL de la página listing misma.

4. Si la página es DETALLE de UNA convocatoria (describe UNA sola con criterios, alcance, deadline, etc.), devuelve EXACTAMENTE UN item usando la URL de la página tal como te llegó.

5. Si la página es info general/institucional/cómo-aplicar SIN convocatoria activa concreta, devuelve {"items": []}.

6. Cada URL debe estar PRESENTE en el contenido que recibiste, EXCEPTO en el caso (4) donde puedes usar la URL de la página misma. No inventes URLs.

7. El campo `brief` es opcional: 1 línea con descripción si la encontrás, o null.

8. ANTE LA DUDA, INCLUYE. Si un item podría ser una convocatoria (aunque el título sea ambiguo, sin descripción, o no esté 100% claro si es llamado activo), inclúyelo. Es mejor incluir de más y dejar que el filtro posterior decida, que perder oportunidades. Solo excluye lo que CLARAMENTE no es un llamado a postularse (menús, footer, redes sociales, páginas obviamente institucionales).

Devuelve JSON:
{"items": [{"title": "título exacto del llamado", "url": "https://...", "brief": "1 línea o null"}]}

No incluyas texto adicional fuera del JSON."""


SYSTEM_PROMPT = f"""Eres un asistente especializado en identificar oportunidades de financiamiento y convocatorias relevantes para organizaciones de consultoría social en Colombia.

Fecha de hoy: {TODAY}. Si una oportunidad menciona una fecha límite que ya pasó o que vence en menos de {DEADLINE_GRACE_DAYS} días, NO la incluyas en los resultados.

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

Responde ÚNICAMENTE con un JSON válido:
{{
  "items": [
    {{
      "url": "url de la oportunidad",
      "title": "título de la oportunidad",
      "reason": "razón en 1 línea de por qué podría ser relevante para Estudio Plural"
    }}
  ]
}}

Si ninguna aplica: {{"items": []}}
No incluyas texto adicional fuera del JSON."""


ENRICH_PROMPT = f"""Dado el contenido de una página web sobre convocatorias/financiamiento, clasifica la página y extrae campos estructurados.

Fecha de hoy: {TODAY}.

Responde ÚNICAMENTE con un JSON válido:
{{
  "page_type": "single_call | listing_page | general_info",
  "deadline": "fecha límite tal como aparece en el texto, 'Convocatoria permanente / sin fecha límite fija', o null si no se menciona",
  "deadline_iso": "fecha límite en formato YYYY-MM-DD, o 'rolling' si es ventanilla abierta sin fecha fija, o null si no se puede determinar",
  "funding_amount": "monto o rango de financiación (con moneda) o null",
  "eligibility": "requisitos administrativos y de elegibilidad resumidos en 1-2 líneas, o null",
  "experience_years": "años de experiencia requeridos o null",
  "themes": ["tema 1", "tema 2"]
}}

`page_type`:
- "single_call": esta página describe UNA convocatoria/licitación específica con criterios, alcance temático o proceso de aplicación concreto
- "listing_page": esta página lista MÚLTIPLES convocatorias activas, cada una con su propio link de detalle
- "general_info": página institucional, guía genérica de "cómo aplicar", info corporativa, contacto, sin llamado activo concreto

Busca el deadline con atención: "fecha límite", "closing date", "deadline", "apply by", "due date", "fecha de cierre", "plazo", o fechas asociadas a verbos de aplicación.

No incluyas texto adicional fuera del JSON."""


# ---------- Helpers ----------

def _strip_json_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()
    return raw


def _cached_system(prompt: str) -> dict:
    return {
        "role": "system",
        "content": [
            {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}
        ],
    }


def _is_deadline_past(deadline_iso: Optional[str]) -> bool:
    if not deadline_iso or deadline_iso == "rolling":
        return False
    try:
        deadline_date = datetime.strptime(deadline_iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return (deadline_date - date.today()).days < DEADLINE_GRACE_DAYS


def _normalize_url(url: str) -> str:
    """Drop fragment, query, trailing slash for self-URL dedup."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


def _url_in_html(url: str, html: str) -> bool:
    """Loose check: URL or its path appears in the original HTML."""
    if url in html:
        return True
    p = urlparse(url)
    if p.path and p.path != "/" and p.path in html:
        return True
    return False


def _clean_html_for_llm(html: str, max_chars: int = HTML_MAX_CHARS) -> str:
    """Convert HTML into compact text with anchors preserved as `[text](url)`."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside",
                     "svg", "form", "noscript", "iframe"]):
        tag.decompose()

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True) or "link"
        href = a["href"]
        a.replace_with(NavigableString(f"[{text}]({href})"))

    main = soup.find(["main", "article"]) or soup.find("body") or soup
    text = main.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text[:max_chars]


# ---------- LLM-driven extraction ----------

def extract_opportunities_llm(
    html: str,
    source_url: str,
    source_name: str,
) -> list[dict]:
    """
    Send cleaned HTML to Haiku and return individual opportunities.
    Returns [] on failure (caller can fall back to BS4).
    """
    cleaned = _clean_html_for_llm(html)
    if not cleaned:
        return []
    logger.debug("Cleaned HTML for %s: %d chars (raw: %d)", source_name, len(cleaned), len(html))

    user_msg = (
        f"URL de la página: {source_url}\n"
        f"Fuente: {source_name}\n\n"
        f"Contenido:\n\n{cleaned}"
    )

    try:
        response = _get_client().chat.completions.create(
            model=EXTRACT_MODEL,
            max_tokens=2048,
            response_format={"type": "json_object"},
            messages=[
                _cached_system(EXTRACT_PROMPT),
                {"role": "user", "content": user_msg},
            ],
        )
        raw = _strip_json_fence(response.choices[0].message.content)
        parsed = json.loads(raw)
        items = parsed.get("items", []) if isinstance(parsed, dict) else []
        if not isinstance(items, list):
            return []
    except Exception as e:
        logger.error("LLM extraction failed for %s: %s", source_url, e)
        return []

    source_url_norm = _normalize_url(source_url)
    today = date.today().isoformat()
    valid: list[dict] = []
    seen_urls: set[str] = set()
    multi_item = len(items) > 1  # listing case → drop self-URL; single → keep it

    for item in items:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        brief = item.get("brief") or ""
        if not title or not url:
            continue

        url = urljoin(source_url, url)
        is_self = _normalize_url(url) == source_url_norm
        if is_self and multi_item:
            # listing page that also returned itself — drop the self-link
            continue
        if not is_self and not _url_in_html(url, html):
            logger.warning("Hallucinated URL %s in extraction from %s, skipping", url, source_name)
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        valid.append({
            "title": title,
            "url": url,
            "description": (brief[:500] if isinstance(brief, str) else ""),
            "source_name": source_name,
            "date_found": today,
        })

    logger.info("LLM extracted %d items from %s (cleaned HTML: %d chars)", len(valid), source_name, len(cleaned))
    return valid


# ---------- Filter pass ----------

def filter_relevant(opportunities: list[dict]) -> list[dict]:
    """Send batches to the filter model. Returns relevant opps enriched with `reason`."""
    if not opportunities:
        return []

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
            "Sending batch %d-%d to %s (%d opportunities)",
            i + 1, i + len(batch), FILTER_MODEL, len(batch),
        )

        try:
            response = _get_client().chat.completions.create(
                model=FILTER_MODEL,
                max_tokens=2048,
                response_format={"type": "json_object"},
                messages=[
                    _cached_system(SYSTEM_PROMPT),
                    {
                        "role": "user",
                        "content": f"Filtra estas oportunidades y devuelve las que vale la pena revisar:\n\n{json.dumps(batch_payload, ensure_ascii=False, indent=2)}",
                    },
                ],
            )
            raw = _strip_json_fence(response.choices[0].message.content)
            parsed = json.loads(raw)
            items = parsed.get("items", []) if isinstance(parsed, dict) else parsed
            if not isinstance(items, list):
                logger.warning("Filter response not a list, skipping batch")
                continue

            url_to_opp = {opp["url"]: opp for opp in batch}
            for item in items:
                url = item.get("url", "") if isinstance(item, dict) else ""
                if url not in url_to_opp:
                    logger.warning("Filter returned unknown URL %r, skipping", url)
                    continue
                relevant.append({**url_to_opp[url], "reason": item.get("reason", "")})

        except json.JSONDecodeError as e:
            logger.error("Failed to parse filter response as JSON: %s", e)
        except Exception as e:
            logger.error("OpenRouter API error during filter: %s", e)

    logger.info("Filter kept %d of %d opportunities", len(relevant), len(opportunities))
    return relevant


# ---------- Enrichment pass ----------

def _enrich_one(opp: dict) -> dict:
    detail_text = opp.get("detail_text", "")
    if not detail_text:
        return opp
    try:
        response = _get_client().chat.completions.create(
            model=ENRICH_MODEL,
            max_tokens=600,
            response_format={"type": "json_object"},
            messages=[
                _cached_system(ENRICH_PROMPT),
                {
                    "role": "user",
                    "content": f"Título: {opp.get('title', '')}\n\nTexto:\n{detail_text}",
                },
            ],
        )
        raw = _strip_json_fence(response.choices[0].message.content)
        fields = json.loads(raw)
        if not isinstance(fields, dict):
            logger.warning("Enrich response not a dict for %s", opp.get("url"))
            return opp
        return {**opp, **fields}
    except Exception as e:
        logger.error("Failed to enrich %s: %s", opp.get("url"), e)
        return opp


def enrich_details(opportunities: list[dict]) -> list[dict]:
    """Extract structured fields in parallel via the enrich model."""
    if not opportunities:
        return []
    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
        enriched = list(pool.map(_enrich_one, opportunities))
    logger.info("Enriched %d opportunities with detail text", len(enriched))
    return enriched


# ---------- Post-enrichment classification ----------

def split_after_enrichment(
    opportunities: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Split enriched opportunities into (single_calls_to_send, listing_pages_to_drill).
    Drops `general_info` pages and `single_call`s with expired deadlines.
    """
    single_calls: list[dict] = []
    listings: list[dict] = []
    dropped_general = 0
    dropped_expired = 0

    for opp in opportunities:
        page_type = opp.get("page_type", "single_call")
        if page_type == "general_info":
            dropped_general += 1
            continue
        if page_type == "listing_page":
            listings.append(opp)
            continue
        # default: treat as single_call
        if _is_deadline_past(opp.get("deadline_iso")):
            dropped_expired += 1
            continue
        single_calls.append(opp)

    if dropped_general:
        logger.info("Dropped %d general_info page(s)", dropped_general)
    if dropped_expired:
        logger.info("Dropped %d expired/imminent deadline opportunity/ies", dropped_expired)
    if listings:
        logger.info("Found %d listing page(s) for drill-in", len(listings))
    return single_calls, listings
