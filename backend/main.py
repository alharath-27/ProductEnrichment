from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pathlib import Path
from collections import deque
import csv
import shutil
import yaml
import logging

from .csv_processing import process_csv_with_category

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s %(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CATEGORIES_PATH = BASE_DIR / "categories.yaml"


def _load_categories() -> dict:
    try:
        data = yaml.safe_load(CATEGORIES_PATH.read_text())
        return data or {}
    except Exception as e:
        logger.error("Failed to load categories.yaml: %s", e, exc_info=True)
        return {}


def _maybe_override_api_key(request: Request) -> None:
    """Allow the UI to pass the API key per session via header."""
    try:
        key = (request.headers.get("X-Deepseek-Api-Key", "") or "").strip()
        if key:
            import os

            os.environ["DEEPSEEK_API_KEY"] = key
    except Exception:
        pass


app = FastAPI(title="Product Enrichment API")

app.mount("/files", StaticFiles(directory=str(UPLOAD_DIR)), name="files")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/categories")
def get_categories():
    return _load_categories()


@app.get("/preview")
def preview(limit: int = 25):
    output_path = UPLOAD_DIR / "output.csv"
    if not output_path.exists():
        return {"rows_processed": 0, "rows": []}

    try:
        safe_limit = max(1, min(int(limit or 25), 100))
        with output_path.open("r", encoding="utf-8", newline="") as f:
            tail = deque(maxlen=safe_limit)
            rows_processed = 0
            for row in csv.DictReader(f):
                rows_processed += 1
                tail.append(row)
        return {
            "rows_processed": rows_processed,
            "rows": list(tail),
        }
    except Exception as e:
        logger.error("Error reading preview: %s", e, exc_info=True)
        return JSONResponse({"error": "Error reading preview: " + str(e)}, status_code=500)


@app.post("/process")
async def process(
    request: Request,
    category: str = Form(...),
    file: UploadFile = File(...),
    row_limit: int = Form(None),
):
    _maybe_override_api_key(request)
    categories = _load_categories()
    if category not in categories:
        return JSONResponse({"error": "Invalid category"}, status_code=400)

    input_path = UPLOAD_DIR / "input.csv"
    try:
        with input_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.error("Error saving file: %s", e, exc_info=True)
        return JSONResponse({"error": "Error saving file: " + str(e)}, status_code=500)

    try:
        output_file = process_csv_with_category(
            input_path=input_path,
            category=category,
            base_dir=BASE_DIR,
            row_limit=row_limit,
        )
    except Exception as e:
        logger.error("Error during CSV processing: %s", e, exc_info=True)
        return JSONResponse({"error": "Error during CSV processing: " + str(e)}, status_code=500)

    return {"download_path": f"/files/{output_file.name}"}
