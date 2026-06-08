"""Product Enrichment UI — upload WooCommerce CSV, pick category, download enriched CSV."""

import io
import os
import time
import threading

import pandas as pd
import requests
import streamlit as st

IN_DOCKER = os.getenv("RUNNING_IN_DOCKER") == "1"
DOCKER_API_URL = "http://api:8000"


def _default_api_url() -> str:
    env = (os.getenv("API_URL") or "").strip().rstrip("/")
    return env or DOCKER_API_URL


DEFAULT_API_URL = _default_api_url()

st.set_page_config(page_title="Product Enrichment", layout="wide")

# http://api:8000 only works inside Docker — clear stale value from local runs
if IN_DOCKER:
    st.session_state["cfg_api_url"] = DEFAULT_API_URL
elif st.session_state.get("cfg_api_url", "").startswith("http://api"):
    st.session_state["cfg_api_url"] = "http://127.0.0.1:8787"

with st.sidebar:
    st.header("Configuration")
    st.caption("Settings apply to this browser session only.")
    if IN_DOCKER:
        st.caption("Docker: API is **woocommerceproductenrichment-api-1** at `http://api:8000`")

    api_url_input = st.text_input(
        "Backend API URL",
        value=st.session_state.get("cfg_api_url", DEFAULT_API_URL),
        help="Leave as http://api:8000 when using Docker (default).",
        key="cfg_api_url_input",
        disabled=IN_DOCKER,
    ).rstrip("/")
    st.session_state["cfg_api_url"] = api_url_input or DEFAULT_API_URL

    st.divider()
    st.subheader("Enrichment")
    st.session_state["cfg_row_limit_default"] = st.number_input(
        "Default rows to process",
        min_value=1,
        step=1,
        value=int(st.session_state.get("cfg_row_limit_default", 5)),
        help="Use 1 to test a single product. Use a large number (e.g. 4000) for full batches.",
    )
    st.session_state["cfg_poll_seconds"] = st.number_input(
        "Live preview refresh (seconds)",
        min_value=1,
        max_value=30,
        step=1,
        value=int(st.session_state.get("cfg_poll_seconds", 2)),
    )

    st.divider()
    st.subheader("API key")
    st.caption("Required unless DEEPSEEK_API_KEY is set in the server .env file.")
    st.session_state["cfg_api_key"] = st.text_input(
        "DEEPSEEK_API_KEY",
        value=st.session_state.get("cfg_api_key", ""),
        type="password",
    )

API_URL = st.session_state.get("cfg_api_url", DEFAULT_API_URL).rstrip("/")

st.title("Medical Device Product Enrichment")
st.markdown(
    "Upload a **WooCommerce product export CSV**, choose a product category, "
    "and generate improved **titles** and **descriptions** using AI."
)

with st.expander("How does this work?", expanded=False):
    st.markdown(
        """
1. Export products from WooCommerce as a CSV.
2. Upload the file below and select the category that matches your products.
3. Set how many rows to process (start with **1** for a quick test).
4. Click **Enrich Products** and wait for the live preview.
5. Download the enriched CSV and import it back into your store.

**Requirements:** A [DeepSeek](https://platform.deepseek.com/) API key and this app running via Docker (see README).
        """
    )


def _api_headers():
    headers = {}
    api_key = (st.session_state.get("cfg_api_key") or "").strip()
    if api_key:
        headers["X-Deepseek-Api-Key"] = api_key
    return headers


def fetch_categories():
    try:
        response = requests.get(f"{API_URL}/categories", headers=_api_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as err:
        st.error(f"Could not load categories from backend: {err}")
        if "resolve" in str(err).lower() or "api" in str(err).lower():
            st.warning(
                "The API container (**woocommerceproductenrichment-api-1**) is not reachable. "
                "From this folder run: `docker compose down && docker compose up --build`"
            )
        return None


def render_csv_preview(label, file_bytes, max_cols=12, max_rows=5):
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    preview_df = None
    for encoding in encodings:
        try:
            preview_df = pd.read_csv(io.BytesIO(file_bytes), nrows=max_rows, encoding=encoding)
            break
        except (UnicodeDecodeError, Exception):
            continue

    if preview_df is None:
        st.warning(f"Could not preview {label}: unable to decode CSV")
        return

    columns = preview_df.columns.tolist()
    if not columns:
        st.caption(f"{label}: no columns detected.")
        return
    preview = ", ".join(columns[:max_cols])
    suffix = "..." if len(columns) > max_cols else ""
    st.caption(f"{label} columns ({len(columns)} total): {preview}{suffix}")
    if not preview_df.empty:
        st.dataframe(preview_df, width="stretch")
    else:
        st.info(f"{label}: no data rows found.")


categories = fetch_categories()

uploaded_file = st.file_uploader("Upload CSV for enrichment", type="csv")
enrich_file_bytes = uploaded_file.getvalue() if uploaded_file else None
if enrich_file_bytes:
    render_csv_preview("Enrichment CSV", enrich_file_bytes)

row_limit = st.number_input(
    "Number of rows to process",
    min_value=1,
    step=1,
    value=int(st.session_state.get("cfg_row_limit_default", 5)),
    help="Set to 1 to enrich a single product. Increase for larger batches.",
)

if categories:
    category = st.selectbox(
        "Category",
        list(categories.keys()),
        format_func=lambda x: categories[x]["label"],
    )

    if enrich_file_bytes and st.button("Enrich Products", type="primary"):
        st.info("Processing… live preview updates as each row completes.")
        files = {"file": ("input.csv", enrich_file_bytes, "text/csv")}
        data = {"category": category, "row_limit": row_limit}
        headers = _api_headers()

        result_holder = {"response": None, "error": None}

        def _run_backend_job():
            try:
                result_holder["response"] = requests.post(
                    f"{API_URL}/process",
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=900,
                )
            except Exception as e:
                result_holder["error"] = str(e)

        t = threading.Thread(target=_run_backend_job, daemon=True)
        t.start()

        status = st.empty()
        progress = st.progress(0)
        st.markdown("#### Live preview (latest rows)")
        preview = st.empty()

        last_seen_rows = 0
        while t.is_alive():
            try:
                poll_url = f"{API_URL}/preview?limit=25&ts={time.time()}"
                preview_response = requests.get(poll_url, headers=headers, timeout=10)
                if preview_response.ok:
                    preview_payload = preview_response.json()
                    preview_rows = preview_payload.get("rows", [])
                    last_seen_rows = int(preview_payload.get("rows_processed", 0) or 0)
                    df_live = pd.DataFrame(preview_rows)
                    cols = [
                        c
                        for c in [
                            "SKU",
                            "sku",
                            "Title",
                            "title",
                            "Name",
                            "name",
                            "Description",
                            "description",
                        ]
                        if c in df_live.columns
                    ]
                    if not df_live.empty:
                        df_show = df_live[cols] if cols else df_live
                        preview.dataframe(df_show, width="stretch", height=420)
                    total = int(row_limit) if row_limit else 0
                    if total > 0:
                        progress.progress(min(1.0, last_seen_rows / max(1, total)))
                    status.caption(f"Generated {last_seen_rows} row(s) so far…")
            except Exception:
                status.caption("Waiting for first rows…")
            time.sleep(int(st.session_state.get("cfg_poll_seconds", 2)))

        try:
            if result_holder["error"]:
                raise RuntimeError(result_holder["error"])
            response = result_holder["response"]
            if response is None:
                raise RuntimeError("Backend job did not return a response.")

            output = response.json()
            if not response.ok or "download_path" not in output:
                raise RuntimeError(output.get("error", "Unexpected response."))

            download_url = f"{API_URL}{output['download_path']}"
            csv_response = requests.get(download_url, headers=headers, timeout=60)
            df = pd.read_csv(io.BytesIO(csv_response.content))
            st.success("Enrichment complete.")
            st.subheader("Sample of enriched products")
            st.dataframe(df.head(10), width="stretch")
            file_name = st.text_input(
                "File name for download (without extension)",
                value="enriched_output",
            )
            st.download_button(
                label="Download full enriched CSV",
                data=csv_response.content,
                file_name=f"{file_name or 'enriched_output'}.csv",
                mime="text/csv",
            )
        except Exception as err:
            st.error(f"Error during enrichment: {err}")
else:
    st.info(
        "Waiting for **woocommerceproductenrichment-api-1**. "
        "In Terminal: `cd \"Woocommerce product enrichment\"` then `docker compose up --build`."
    )
