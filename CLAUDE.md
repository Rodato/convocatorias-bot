# CLAUDE.md — Convocatorias Bot (Estudio Plural)

## ¿Qué hace este proyecto?

Bot que escanea diariamente ~15 sitios web de convocatorias y financiamiento, filtra los resultados por relevancia para **Estudio Plural** usando Claude AI (vía OpenRouter), y envía las oportunidades relevantes al canal `#convocatorias` de Slack.

**Problema que resuelve:** Yeison hacía esta búsqueda manualmente todos los días en ~20 sitios. El bot lo automatiza con un cron en GitHub Actions.

---

## Stack

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.9+ |
| HTTP/HTML | `requests` + `BeautifulSoup4` (lxml) |
| Extracción | LLM-driven (Haiku) sobre HTML limpio + fallback de source URL |
| LLMs | Haiku 4.5 (extract + filter) + Sonnet 4.6 (enrich) vía OpenRouter |
| LLM SDK | `openai` (OpenRouter es compatible con la API de OpenAI) |
| Notificaciones | Slack Incoming Webhook |
| Scheduling | GitHub Actions (cron diario 13:00 UTC = 8:00am Colombia) |
| Estado/deduplicación | MongoDB (`convocatorias_bot.opportunities`) |
| Entorno local | `python3 -m venv .venv` |

---

## Estructura de archivos

```
convocatorias-bot/
├── .github/
│   └── workflows/
│       └── daily_scan.yml   # Cron diario + Slack alert on failure
├── src/
│   ├── main.py              # Orquestador con paralelización + drill-in
│   ├── scraper.py           # HTTP + BS4 fallback + fetch_detail
│   ├── analyzer.py          # extract_opportunities_llm + filter_relevant + enrich_details
│   ├── notifier.py          # Slack Block Kit con _format_deadline (prefiere ISO)
│   └── storage.py           # MongoDB persistence (filter_seen, save_new_opportunities)
├── data/
│   └── sources.json         # Lista de fuentes (nombre, URL, tipo)
├── config.py                # ORG_PROFILE + KEYWORDS para el prompt de Claude
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## Flujo de ejecución

```
main.py
  1. Carga data/sources.json
  2. Para cada fuente (paralelo, ThreadPoolExecutor):
       a. fetch_html(url) → HTML crudo (con retry/backoff en 5xx/timeout)
       b. analyzer.extract_opportunities_llm(html, url, name)
            — Limpia HTML (drop nav/script/style/svg), preserva links como [text](url)
            — Truncar a 8000 chars
            — Haiku con prompt cacheado: extrae [{title, url, brief}]
            — Valida: cada URL debe estar en el HTML original (anti-hallucination)
            — Drop self-links normalizados a la URL fuente
       c. Si LLM extrae 0 → queue la URL fuente como candidato individual
            (la enrichment la clasificará como single_call/listing_page/general_info)
  3. storage.filter_seen(scraped_urls) → set de URLs ya en MongoDB ($in query)
       new_opportunities = scraped - seen
  4. analyzer.filter_relevant(new_opportunities)
       — Batches de 20 a Haiku con system prompt cacheado
       — Devuelve solo las relevantes con campo `reason`
  5. Para cada relevante (paralelo):
       a. scraper.fetch_detail(url) → texto limpio del detalle (3000 chars)
       b. analyzer.enrich_details() → Sonnet extrae:
            page_type ∈ {single_call, listing_page, general_info}
            deadline (texto), deadline_iso (YYYY-MM-DD | rolling | null)
            funding_amount, eligibility, experience_years, themes
  6. analyzer.split_after_enrichment():
       — single_call con deadline OK → keep
       — listing_page → mover a expansion_queue
       — general_info → drop
       — deadline_iso pasado o <7 días → drop
  7. Drill-in (cap recursión = 1):
       Para cada listing en expansion_queue:
         a. fetch_html → extract_opportunities_llm de nuevo (sub-fuente: listing.url)
         b. dedup contra seen + nuevos del run
         c. filter_relevant → enrich_details → split (sin más recursión)
         d. merge single_calls resultantes
  8. notifier.send_to_slack(single_calls)
       — _format_deadline prefiere deadline_iso con días-restantes
       — chunks de 23 por mensaje (Slack cap 50 blocks)
       — 0 relevantes → mensaje "Sin nuevas convocatorias hoy"
  9. storage.save_new_opportunities(new_opportunities, single_calls)
       — Solo escribe URLs nuevas (no sobrescribe is_relevant existente)
       — Relevantes → guardan campos enriquecidos + page_type + sent_to_slack=True
```

---

## Variables de entorno

| Variable | Descripción |
|---|---|
| `OPENROUTER_API_KEY` | API key de OpenRouter (`sk-or-...`) |
| `SLACK_WEBHOOK_URL` | URL del Incoming Webhook de Slack |
| `MONGODB_URI` | URI de conexión a MongoDB Atlas |
| `LOG_LEVEL` | `INFO` por default; `DEBUG` para troubleshooting |

Copiar `.env.example` → `.env` y llenar los valores.

---

## Comandos

### Setup inicial
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Editar .env con las credenciales
```

### Correr el bot
```bash
.venv/bin/python3 src/main.py
```

### Resetear MongoDB (forzar re-envío de todo)
```bash
.venv/bin/python3 -c "
from dotenv import load_dotenv
load_dotenv('.env')
from src.storage import _get_collection
_get_collection().delete_many({})
print('Cleared')
"
```

---

## Fuentes configuradas (`data/sources.json`)

LLM extrae limpio en sources estáticas con HTML claro (Patrimonio Natural, Red ADELCO).
Sources JS-heavy o anti-bot devuelven 0 → caen en source-URL fallback (la enrichment las clasifica como general_info y se descartan, o drill-in falla por JS).

| Fuente | Estado | Notas |
|---|---|---|
| Patrimonio Natural | ✅ OK | LLM extrae todos los items con títulos reales |
| Red ADELCO | ✅ OK | LLM extrae 3 convocatorias limpias |
| CEPF Grants | ⚠️ 0 LLM items | URL específica de un call; cae en source URL fallback |
| GIZ Colombia | ⚠️ 0 LLM items | Hub estático, layout complejo |
| Banco Mundial Colombia | ⚠️ 0 LLM items | Hub con filtros JS |
| Open Society Foundations | ⚠️ 0 LLM items | Hub con filtros |
| Grand Challenges | ⚠️ 0 LLM items | Hub estático |
| UN Partner Portal | ⚠️ 0 LLM items | SPA, content por JS |
| UNICEF Colombia | ⚠️ 0 LLM items | A veces 403 anti-bot |
| IKI Small Grants | ⚠️ 0 LLM items | Posts de info sessions, no listado real |
| Gates Foundation | ⚠️ 0 LLM items | SPA, requiere render JS |
| Impact Funding Substack | ⚠️ 0 LLM items | Requiere suscripción |
| IDRC Funding | ✅ Correcto | Página "applying" general → enrichment marca como general_info y descarta |
| UNDP Colombia | ❌ 403 | Anti-bot, requiere Playwright o sesión |
| APC Colombia | ❌ 503 | Servidor inestable |

**Fuentes omitidas en v1** (requieren login): BEO IDB, SECOP, DIAN Fondo.

---

## GitHub Actions

Archivo: `.github/workflows/daily_scan.yml`

- **Trigger automático:** `0 13 * * *` (13:00 UTC = 8:00am Colombia)
- **Trigger manual:** `workflow_dispatch`
- **Secrets requeridos:** `OPENROUTER_API_KEY`, `SLACK_WEBHOOK_URL`, `MONGODB_URI`
- **Notificación de fallas:** step adicional postea a Slack si el job falla.

---

## Personalización

### Agregar una fuente
Editar `data/sources.json`:
```json
{"name": "Nombre visible", "url": "https://...", "type": "html"}
```

### Ajustar perfil y keywords
Editar `config.py`:
- `ORG_PROFILE`: descripción de Estudio Plural para el system prompt
- `KEYWORDS`: palabras clave que guían el filtro

### Cambiar modelos
En `src/analyzer.py`:
```python
EXTRACT_MODEL = "anthropic/claude-haiku-4.5"   # extracción de listings
FILTER_MODEL  = "anthropic/claude-haiku-4.5"   # filtro de relevancia
ENRICH_MODEL  = "anthropic/claude-sonnet-4.6"  # extracción de campos estructurados
```

---

## Estado actual (2026-04-29)

- ✅ Pipeline LLM-driven con extracción smart funcionando.
- ✅ Drill-in implementado (cap recursión = 1) para listing pages.
- ✅ page_type classification (single_call / listing_page / general_info).
- ✅ Prompt caching ephemeral en system messages (Haiku + Sonnet).
- ✅ Paralelización: scrape de fuentes y enrichment.
- ✅ Storage bug fixed (`save_new_opportunities` solo escribe URLs nuevas).
- ✅ Notificación Slack en falla del workflow.
- ✅ requirements.txt pineado.
- ⚠️ Hub-JS sources no extraen items individuales — requieren Playwright o per-source selectors.
- ⚠️ MongoDB tiene historial sucio del primer run con BS4 — no afecta output (rel=False).
