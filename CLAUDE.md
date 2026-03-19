# CLAUDE.md — Convocatorias Bot (Estudio Plural)

## ¿Qué hace este proyecto?

Bot que escanea diariamente ~15 sitios web de convocatorias y financiamiento, filtra los resultados por relevancia para **Estudio Plural** usando Claude AI (vía OpenRouter), y envía las oportunidades relevantes al canal `#convocatorias` de Slack.

**Problema que resuelve:** Yeison hacía esta búsqueda manualmente todos los días en ~20 sitios. El bot lo automatiza con un cron en GitHub Actions.

---

## Stack

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.9+ |
| Scraping | `requests` + `BeautifulSoup4` (lxml) |
| LLM | Claude `anthropic/claude-sonnet-4.6` vía OpenRouter |
| LLM SDK | `openai` (OpenRouter es compatible con la API de OpenAI) |
| Notificaciones | Slack Incoming Webhook |
| Scheduling | GitHub Actions (cron diario 13:00 UTC = 8:00am Colombia) |
| Estado/deduplicación | `data/seen.json` commiteado al repo |
| Entorno local | `python3 -m venv .venv` |

---

## Estructura de archivos

```
convocatorias-bot/
├── .github/
│   └── workflows/
│       └── daily_scan.yml   # Cron + commit automático de seen.json
├── src/
│   ├── main.py              # Orquestador: scrape → dedup → analyze → notify → save
│   ├── scraper.py           # Fetch + extracción heurística de oportunidades
│   ├── analyzer.py          # Filtro de relevancia con Claude vía OpenRouter
│   └── notifier.py          # Envío a Slack con Block Kit
├── data/
│   ├── sources.json         # Lista de fuentes (nombre, URL, tipo)
│   └── seen.json            # URLs ya procesadas — persiste entre runs via git
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
  2. scraper.py: GET cada URL → extrae {title, url, description, source_name, date_found}
        Estrategias (en orden): <article> → <h2>/<h3> → <li>
        Cap: 30 oportunidades por fuente, timeout 15s
  3. Filtra URLs ya en data/seen.json
  4. analyzer.py: envía a OpenRouter en batches de 20
        System prompt: ORG_PROFILE + KEYWORDS (config.py)
        Respuesta esperada: JSON [{url, title, reason, deadline, funding_amount, eligibility, experience_years, themes}]
        max_tokens: 4096 (ampliado para acomodar campos nuevos)
  5. notifier.py: POST al webhook de Slack (Block Kit)
        Campos mostrados: título, fuente, relevancia + deadline, monto, elegibilidad, experiencia, temáticas (si no son null)
        0 relevantes → mensaje "Sin nuevas convocatorias hoy"
  6. Actualiza data/seen.json con TODAS las URLs scrapeadas (no solo las relevantes)
```

---

## Variables de entorno

| Variable | Descripción |
|---|---|
| `OPENROUTER_API_KEY` | API key de OpenRouter (`sk-or-...`) |
| `SLACK_WEBHOOK_URL` | URL del Incoming Webhook de Slack |

Copiar `.env.example` → `.env` y llenar los valores. El `.env` está en `.gitignore`.

---

## Comandos

### Setup inicial
```bash
python3 -m venv .venv
source .venv/bin/activate       # macOS/Linux
# .venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env
# Editar .env con las credenciales
```

### Correr el bot
```bash
# Con venv activo:
python3 src/main.py

# O directamente:
.venv/bin/python3 src/main.py
```

### Resetear seen.json (forzar re-envío de todo)
```bash
echo "[]" > data/seen.json
```

---

## Fuentes configuradas (`data/sources.json`)

| Fuente | Estado | Notas |
|---|---|---|
| UN Partner Portal | ✅ OK | |
| UNDP Colombia | ❌ 403 | Anti-bot, requiere Playwright o sesión |
| CEPF Grants | ✅ OK | Extrae muchos links de navegación, Claude filtra |
| GIZ Colombia | ✅ OK | |
| UNICEF Colombia | ❌ 403 | Anti-bot |
| Banco Mundial Colombia | ✅ OK | |
| IDRC Funding | ✅ OK | |
| IKI Small Grants | ✅ OK | |
| APC Colombia | ❌ 503 | Servidor inestable |
| Grand Challenges | ✅ OK | |
| Open Society Foundations | ✅ OK | |
| Patrimonio Natural | ✅ OK | |
| Red ADELCO | ✅ OK | |
| Gates Foundation | ⚠️ 0 resultados | JS-heavy, no extrae con BS4 |
| Impact Funding Substack | ⚠️ 0 resultados | Requiere suscripción o JS |

**Fuentes omitidas en v1** (requieren login): BEO IDB, SECOP, DIAN Fondo.

---

## GitHub Actions

Archivo: `.github/workflows/daily_scan.yml`

- **Trigger automático:** `0 13 * * *` (13:00 UTC = 8:00am Colombia)
- **Trigger manual:** `workflow_dispatch` desde la pestaña Actions del repo
- **Secrets requeridos en el repo:**
  - `OPENROUTER_API_KEY`
  - `SLACK_WEBHOOK_URL`
- Después de correr, hace `git commit + push` de `data/seen.json` automáticamente

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
- `KEYWORDS`: lista de palabras clave que guían el filtro de Claude

### Cambiar modelo de IA
En `src/analyzer.py`, cambiar la constante `MODEL`:
```python
MODEL = "anthropic/claude-sonnet-4.6"   # actual
# MODEL = "openai/gpt-4o"               # alternativa via OpenRouter
```

---

## Resultado del primer run (2026-03-18)

- 65 oportunidades scrapeadas de 12 fuentes activas
- **14 marcadas como relevantes** por Claude
- Mensaje enviado a Slack exitosamente
- `seen.json` actualizado con 65 URLs
