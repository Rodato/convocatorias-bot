# Convocatorias Bot — Estudio Plural

Bot que escanea diariamente ~15 fuentes de financiamiento y convocatorias, filtra por relevancia usando Claude AI, y notifica al canal `#convocatorias` de Slack.

## Setup

### 1. Clonar e instalar dependencias

```bash
git clone <repo-url>
cd convocatorias-bot
pip3 install -r requirements.txt
```

### 2. Configurar Slack Incoming Webhook

1. Ir a [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Nombre: `Convocatorias Bot` | Workspace: el de Estudio Plural
3. En el menú lateral: **Features → Incoming Webhooks** → activar el toggle
4. Click en **Add New Webhook to Workspace** → seleccionar el canal `#convocatorias`
5. Copiar la URL generada (formato: `https://hooks.slack.com/services/T.../B.../...`)

### 3. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus API keys:
#   OPENROUTER_API_KEY=sk-or-...
#   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### 4. Prueba local

```bash
python3 src/main.py
```

Verifica que llegue un mensaje a `#convocatorias`. En el segundo run, las mismas URLs no deben re-enviarse (gracias a `data/seen.json`).

---

## Configuración en GitHub

### Secrets requeridos

| Secret | Descripción |
|--------|-------------|
| `OPENROUTER_API_KEY` | API key de OpenRouter |
| `SLACK_WEBHOOK_URL` | URL del Incoming Webhook de Slack |

Para agregarlos: **Repo → Settings → Secrets and variables → Actions → New repository secret**

### GitHub Actions

El workflow `.github/workflows/daily_scan.yml` se ejecuta automáticamente a las **8:00am hora Colombia** (13:00 UTC) todos los días.

Para dispararlo manualmente: **Actions → Daily Convocatorias Scan → Run workflow**

---

## Estructura

```
convocatorias-bot/
├── .github/workflows/daily_scan.yml   # Cron diario + commit de seen.json
├── src/
│   ├── main.py       # Orquestador
│   ├── scraper.py    # Extracción de oportunidades por URL
│   ├── analyzer.py   # Filtro de relevancia con Claude
│   └── notifier.py   # Envío a Slack
├── data/
│   ├── sources.json  # Lista de fuentes a escanear
│   └── seen.json     # URLs ya procesadas (evita duplicados)
├── config.py         # Perfil org + palabras clave
└── requirements.txt
```

## Agregar o quitar fuentes

Editar `data/sources.json`. Cada entrada requiere:
```json
{
  "name": "Nombre visible",
  "url": "https://...",
  "type": "html"
}
```

## Ajustar perfil y palabras clave

Editar `config.py` — `ORG_PROFILE` y `KEYWORDS` controlan el criterio de relevancia que usa Claude.
