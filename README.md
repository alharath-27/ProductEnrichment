# WooCommerce Product Enrichment

Internal tool for improving WooCommerce product titles and descriptions.

Upload a WooCommerce product export, choose a product category, run enrichment, and download a CSV that can be imported back into WooCommerce.

## What This Does

- Takes a WooCommerce CSV export.
- Uses product attributes as the source of truth.
- Rewrites the product title and description.
- Keeps the same CSV structure for import.
- Leaves all non-copy columns unchanged.

It does not manage inventory, edit attributes, or create categories.

## Local Setup

Requirements:

- Docker Desktop
- DeepSeek API key
- WooCommerce CSV export

Run:

```bash
cp .env.example .env
# edit .env and add DEEPSEEK_API_KEY
chmod +x start.sh
./start.sh
```

Open:

```text
http://localhost:8501
```

Stop:

```bash
docker compose down
```

## How To Use

1. Export products from WooCommerce.
2. Open the app.
3. Upload the CSV.
4. Pick the correct product category.
5. Start with 1 row as a test.
6. Click **Enrich Products**.
7. Download the enriched CSV.
8. Import the CSV back into WooCommerce.

The category matters. It controls which title and description rules are used.

See category notes: [docs/CATEGORIES.md](docs/CATEGORIES.md)

## Output

The output is the original CSV with enriched copy written back into it.

| Column | What happens |
|---|---|
| `Name` or `Title` | Replaced with the enriched title |
| `Description` | Replaced with the enriched description |
| Everything else | Preserved |

## Title And Description Rules

Titles should be consistent within each category.

For Needles & Scalpels, titles are built from attributes in a fixed format, for example:

```text
Percutaneous Access Needle, 18 Gauge, Thin Wall, 7.0 cm Length, Polycarbonate, Pink, 25/Bag, by Chamfr
```

Descriptions can vary more. They use the category YAML prompts under `backend/prompts/`.

Attributes are treated as the source of truth. If the old product name conflicts with attributes, the attributes win.

## QA Mode

Set this in `.env`:

```env
QA_MODE=standard
```

Options:

- `standard`: faster and cheaper. Runs code validation first and only uses extra AI review when needed.
- `strict`: slower and more expensive. Adds more AI review passes.

## Deployment

For a simple link your boss can open, deploy this repo to a Linux server with Docker.

Server deployment guide:

[DEPLOYMENT.md](DEPLOYMENT.md)

Workflow explanation:

[ENRICHMENT_WORKFLOW.md](ENRICHMENT_WORKFLOW.md)

Production command:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Local/IP test command:

```bash
docker compose up -d --build
```

## Project Files

| File | Purpose |
|---|---|
| `streamlit_app.py` | Browser UI |
| `backend/main.py` | API |
| `backend/csv_processing.py` | CSV pipeline |
| `backend/llm_client.py` | AI calls and validation |
| `backend/prompts/` | Category title/description rules |
| `docker-compose.yml` | Local Docker run |
| `docker-compose.prod.yml` | Server Docker run |
| `Caddyfile` | HTTPS/domain routing for production |

## Notes

- Do not commit `.env`.
- Test with 1 row before running a full catalog.
- Uploaded files are stored in `backend/uploads/` inside Docker.
