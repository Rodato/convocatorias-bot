# Roadmap — Convocatorias Bot

## v2 — Pendientes prioritarios

---

### 1. Integración con LinkedIn (vía Apify)

LinkedIn bloquea scraping directo de forma muy agresiva. La solución es usar **Apify** como intermediario.

**Actor recomendado:** `apify/linkedin-jobs-scraper` o `bebity/linkedin-jobs-scraper`

**Qué necesitamos:**
- Cuenta en [apify.com](https://apify.com) + `APIFY_API_TOKEN` (nuevo secret en GitHub)
- Definir los search queries: ej. `"consultoría social Colombia"`, `"diagnóstico género Colombia"`, `"comunicación para el desarrollo"`
- Llamar al actor vía API REST de Apify y procesar el resultado como cualquier otra fuente

**Cómo integrarlo:**
```python
# En src/scraper.py — nueva función scrape_linkedin_apify()
# POST https://api.apify.com/v2/acts/apify~linkedin-jobs-scraper/runs
# con payload: { "queries": [...], "location": "Colombia" }
# Leer resultado del dataset generado por el run
```

**Costo estimado:** ~$5–15/mes dependiendo del volumen de búsquedas.

---

### 2. Base de datos persistente (reemplazar seen.json)

Actualmente `seen.json` se commitea al repo en cada run. Funciona para el MVP, pero tiene limitaciones:
- Sin historial de por qué se descartó algo
- Sin metadata enriquecida (fecha, fuente, score de relevancia)
- Conflictos de git si se corre en paralelo

**Opciones para reemplazarlo:**

| Opción | Pros | Contras |
|---|---|---|
| **Supabase** (PostgreSQL managed) | Free tier generoso, SQL, fácil de consultar | Requiere setup |
| **PlanetScale / Turso** | Serverless, muy barato | Less familiar |
| **SQLite en repo** | Sin infraestructura extra | No escala, igual problema de git |
| **Redis (Upstash)** | Ultra rápido para dedup | Solo key-value, sin historial rico |

**Recomendación: Supabase** — free tier suficiente, da SQL para consultas futuras.

**Esquema sugerido:**

```sql
-- Tabla principal de oportunidades vistas
CREATE TABLE opportunities (
  id          SERIAL PRIMARY KEY,
  url         TEXT UNIQUE NOT NULL,
  title       TEXT,
  source_name TEXT,
  date_found  DATE,
  relevant    BOOLEAN,
  reason      TEXT,          -- razón de Claude si fue relevante
  sent_to_slack BOOLEAN DEFAULT FALSE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

**Qué cambia en el código:**
- `src/main.py`: reemplazar `load_seen()` / `save_seen()` por queries a Supabase
- Nuevo secret: `SUPABASE_URL` + `SUPABASE_KEY`
- Nueva dependencia: `supabase` (Python SDK)

**Beneficios adicionales:**
- Historial completo de todo lo scrapeado
- Poder consultar "¿qué oportunidades de UNDP encontramos en febrero?"
- Base para un dashboard futuro

---

### 3. Correr completamente solo (checklist)

Para que el bot sea 100% autónomo sin intervención manual:

- [ ] **Secrets en GitHub** configurados: `OPENROUTER_API_KEY`, `SLACK_WEBHOOK_URL`
- [ ] **Permisos del workflow** — el repo debe permitir que Actions escriba (`contents: write` ya está en el yml)
- [ ] **Branch protections** — si se activa "require PR", el `git push` del bot falla. Dejar `main` sin protección o agregar el bot como bypass
- [ ] **Monitoreo de fallos** — GitHub Actions envía email al owner si el workflow falla. Verificar que el email de la cuenta Rodato esté activo
- [ ] **Alertas de rate limit** — si OpenRouter o las fuentes empiezan a bloquear, agregar notificación a Slack cuando hay errores en >3 fuentes
- [ ] **Renovación de API keys** — calendarisar revisión trimestral de keys

---

### 4. Otras mejoras futuras

- **Playwright para fuentes JS-heavy** — Gates Foundation, Impact Funding Substack, UNDP, UNICEF
- **Score de relevancia numérico** — que Claude devuelva un 1–10 además de la razón, para priorizar en Slack
- **Digest semanal** — resumen de las mejores oportunidades de la semana, no solo las nuevas del día
- **Fuentes v2** — BEO IDB, SECOP, DIAN Fondo (requieren login/sesión)
- **Dashboard** — interfaz simple sobre Supabase para ver historial de convocatorias
