# WooCommerce Product Enrichment

**What you get:** A spreadsheet of your catalog with improved **product titles** and **descriptions**, ready to import back into WooCommerce.

**What it is:** An internal tool for medical-device products. You export from the store, run enrichment in a browser, download the result. AI writes the copy; rules and checks try to keep specs accurate and tied to each SKU.

**What it is not:** Inventory management, attribute editing, or category management. Those live in the larger Product Workbench; this repo only does title + description enrichment.

---

## At a glance

| | |
|---|---|
| **Input** | WooCommerce product export (CSV) |
| **Output** | Same format, enriched — new `title` and `description` per row |
| **Who runs it** | Catalog / ops (browser UI) |
| **How it runs** | Docker on a laptop or server (two containers: UI + API) |
| **AI provider** | [DeepSeek](https://platform.deepseek.com/) (API key required) |
| **Cost driver** | AI calls per product; `QA_MODE=standard` is faster/cheaper, `QA_MODE=strict` is slower/safer |

---

## How to use the product

### Before you start

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running  
- A DeepSeek API key  
- A WooCommerce CSV export (include SKU, Name, descriptions, and product attributes)

### Start the app (once per machine)

```bash
cd "Woocommerce product enrichment"
cp .env.example .env
# Add your key: DEEPSEEK_API_KEY=sk-...
chmod +x start.sh
./start.sh
```

Open **http://localhost:8501**

Stop when finished: `docker compose down`

### QA mode

Set this in `.env`:

```env
QA_MODE=standard
```

- `standard` — faster and cheaper; code validation runs first and AI reviewer calls happen only when needed.
- `strict` — slower and more expensive; adds extra AI readiness/review passes for high-risk batches.

### Deploy for a boss-friendly URL

For the reusable server version, put this repo in a private GitHub repo and deploy it to a Linux server with Docker and Caddy.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full handoff path:

```text
Private GitHub repo
→ Linux server with Docker
→ docker compose -f docker-compose.prod.yml up -d --build
→ Caddy serves HTTPS
→ Boss opens the app URL in a browser
```

### Enrich products (every batch)

1. **Export** from WooCommerce → Products → Export (save as `.csv`).
2. **Open** http://localhost:8501
3. **Upload** the CSV.
4. **Choose category** — must match the product type (catheters, guidewires, balloons, etc.). Wrong category = poor copy. Full list: [docs/CATEGORIES.md](docs/CATEGORIES.md)
5. **Set row count** — use **1** for a pilot; increase for production batches.
6. **Click** Enrich Products — watch the live preview.
7. **Download** the enriched CSV.
8. **Import** into WooCommerce using your usual import workflow.

### Pilot vs production

| Run type | Rows | Why |
|----------|------|-----|
| Pilot | 1–5 | Verify category, tone, and specs before spend |
| Batch | As needed | Full catalog; longer runtime and API cost |

---

## End-to-end flow

```
WooCommerce store
      │
      │  export CSV
      ▼
┌─────────────────┐
│  Web UI :8501   │  upload · pick category · set row limit
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  API :8787      │  process each product (AI + validation)
└────────┬────────┘
         │
         │  download enriched CSV
         ▼
WooCommerce store  (import updated titles & descriptions)
```

**URLs**

| Service | URL |
|---------|-----|
| Web app (daily use) | http://localhost:8501 |
| API health check | http://localhost:8787/health |

---

## What’s in the output file

The downloaded CSV keeps the same structure as the input WooCommerce export. The app writes the enriched copy back into the product title/name and description columns.

| Input column | Output behavior |
|--------------|-----------------|
| `Name` or `Title` | Replaced with the enriched product title |
| `Description` | Replaced with the enriched product description |
| All other columns | Preserved from the original input file |

QC details are still tracked internally while the batch runs, but the import file stays clean so it can go back into WooCommerce.

---

## Under the hood (technical steps)

For **each product (one CSV row)**, the system runs the same pipeline. No manual steps between rows.

### Stage 1 — Gather product data

- Read SKU, name, and descriptions from the export.
- Collect WooCommerce attribute columns (`Attribute 1 name` / `value`, etc.).
- Optionally extract extra hints from existing text (not treated as official specs).
- Flag obvious contradictions (e.g. attributes say “round” but the name says “flat”).

**Principle:** Structured attributes are the source of truth for specs. Existing copy is context only.

### Stage 2 — Generate and check the title

1. AI writes a title using the category-specific template (22 medical-device types).
2. The prompt forces consistent title slot order and separators for that selected category.
3. Automated cleanup (units, dimensions, formatting).
4. **Code validation** — numbers and measurements must match attribute values exactly.
5. AI **review** — used when validation finds issues, or when `QA_MODE=strict`.
6. Retry review/regeneration if checks fail (stricter for some categories, e.g. polymer tubing).

### Stage 3 — Generate and check the description

Same pattern as the title, using the **new title** so copy stays consistent:

1. AI writes the description from category rules + attributes + title.
2. Review and code validation (same grounding rules).
3. Additional review rounds only if validation still fails.

### Stage 4 — Write result

- Append the row to the output file immediately (powers live preview in the UI).
- Every 50 rows, write a small QC summary for batch monitoring.

### Quality model (why multiple AI calls)

| Layer | Role |
|-------|------|
| **Generation** | Produces title and description from templates |
| **Review** | Second AI pass catches wrong specs or SKU mix-ups |
| **Validation** | Code enforces “only use numbers/specs from attributes” |

This trades API cost for fewer bad imports. Expect **~4–8+ DeepSeek calls per product** when reviews and retries run.

### Architecture (for engineering)

| Piece | Technology |
|-------|------------|
| UI | Streamlit (`streamlit_app.py`) |
| API | FastAPI (`backend/main.py`) — `/categories`, `/process` |
| Prompts | Per-category YAML under `backend/prompts/` |
| Runtime | Docker Compose — `api` + `ui` containers |

Optional local setup without Docker: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)

---

## Operations notes

**API key** — Set in `.env` on the server, or paste in the UI sidebar (session only). Never commit `.env`.

**Data** — Uploaded files sit in `backend/uploads/` inside Docker. Clear periodically if exports are sensitive.

**Troubleshooting**

| Issue | Action |
|-------|--------|
| UI won’t load categories | Run `./start.sh` from this folder; confirm Docker is running |
| Enrichment errors | Check API key; retry with 1 row |
| Weak copy | Wrong category — see [docs/CATEGORIES.md](docs/CATEGORIES.md) |
| Port in use | `docker compose down --remove-orphans` then `./start.sh` |

---

## Scope

Included: CSV in → enriched titles/descriptions → CSV out.

Not included: inventory tools, bulk attribute editor, category generator UI.
