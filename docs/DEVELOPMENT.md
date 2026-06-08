# Development setup (optional)

Most users should use **Docker** (see [README.md](../README.md)). Use this guide only if you prefer running Python directly on your machine.

## Prerequisites

- Python 3.11+
- DeepSeek API key in environment or `.env`

## Install

```bash
cd product-enrichment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set DEEPSEEK_API_KEY
```

## Run (two terminals)

**Terminal 1 — API:**

```bash
export PYTHONPATH="$(pwd)"
export DEEPSEEK_API_KEY=your_key_here
uvicorn backend.main:app --host 127.0.0.1 --port 8787 --reload
```

**Terminal 2 — UI:**

```bash
export API_URL=http://127.0.0.1:8787
streamlit run streamlit_app.py
```

Open http://localhost:8501 and set **Backend API URL** to `http://127.0.0.1:8787`.

## Health check

```bash
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/categories
```
