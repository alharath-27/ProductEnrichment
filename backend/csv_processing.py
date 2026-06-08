import yaml
import pandas as pd
from pathlib import Path
import json
import logging
import re
import csv as csv_module
import os
from collections import Counter

from .llm_client import call_title_llm, call_description_llm

logger = logging.getLogger(__name__)

_MATERIAL_HINTS = [
    "304V",
    "316L",
    "17-7",
    "17-7PH",
    "MP35N",
    "Nitinol",
    "Nickel-Titanium",
    "Tungsten",
    "Platinum",
    "Iridium",
    "Cobalt",
]


def _normalize_text(text: str) -> str:
    t = _safe_str(text)
    t = t.replace("\u00A0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _try_float(s: str):
    try:
        return float(s)
    except Exception:
        return None


def _normalize_sku(value) -> str:
    """Prefer stable SKU strings (avoid trailing .0 from pandas floats)."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    # Handle pandas float SKUs like 1362204.0
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    s = str(value).strip()
    if s.endswith(".0") and s.replace(".", "", 1).isdigit():
        return s[:-2]
    return s


def _extract_attributes_from_free_text(name: str, short_desc: str, desc: str) -> dict:
    """
    Extract structured hints from free-text Name/Description fields.
    Never invents values: only parses what is explicitly present.
    """
    text = " | ".join([_normalize_text(name), _normalize_text(short_desc), _normalize_text(desc)])
    text_l = text.lower()
    if not text.strip("| ").strip():
        return {}

    derived: dict = {}

    # --- Wire shape hint ---
    if re.search(r"\bround\b", text_l):
        derived["Wire Shape (parsed)"] = "Round"
    if re.search(r"\bflat\b", text_l) or re.search(r"\brolled\s+flat\b", text_l):
        derived["Wire Shape (parsed)"] = "Flat"

    # --- Temper / condition (simple keyword-based) ---
    if "superelastic" in text_l:
        derived["Temper/Condition"] = "Superelastic"
    elif re.search(r"\bspring\b", text_l):
        derived["Temper/Condition"] = "Spring Temper"

    # --- Surface finish ---
    if re.search(r"\bbright\b", text_l):
        derived["Surface Finish"] = "Bright Finish"
    if re.search(r"black\s*oxide", text_l):
        derived["Surface Finish"] = "Black Oxide Finish"
    if re.search(r"oxide[-\s]*free", text_l):
        derived["Surface Finish"] = "Oxide-Free"

    # --- Ends (e.g. "3 ends") ---
    m = re.search(r"\b(\d+)\s*ends?\b", text_l)
    if m:
        derived["Ends"] = m.group(1)

    # --- Packaging quantity (e.g. "set of 16 bobbins") ---
    m = re.search(r"\bset\s+of\s+(\d+)\s*bobbins?\b", text_l)
    if m:
        derived["Packaging (parsed)"] = f"1 set of {m.group(1)} bobbins"

    # --- Tensile (ksi) ---
    # Examples: "325 min. ksi", "300 ksi min", "≥300 ksi"
    m = re.search(r"(≥|>=)?\s*(\d{3}(?:\.\d+)?)\s*(?:ksi)\b", text_l)
    if m:
        prefix = m.group(1) or ""
        value = m.group(2)
        # Look around the match for "min/minimum"
        window_start = max(0, m.start() - 15)
        window_end = min(len(text_l), m.end() + 15)
        window = text_l[window_start:window_end]
        if "min" in window or "minimum" in window or prefix in ("≥", ">="):
            derived["Tensile Range (ksi)"] = f"{'≥' if prefix in ('≥','>=') else ''}{value} ksi min."
        else:
            derived["Tensile Range (ksi)"] = f"{value} ksi"

    # --- Lengths (ft) ---
    m = re.search(r"\b([\d,]+(?:\.\d+)?)\s*ft\.?\s*(?:per\s*end|/end)\b", text_l)
    if m:
        derived["Length per End (ft)"] = m.group(1).replace(",", "")
    m = re.search(r"\b([\d,]+(?:\.\d+)?)\s*ft\.?\s*(?:per\s*spool|/spool)\b", text_l)
    if m:
        derived["Total Length per Spool (ft)"] = m.group(1).replace(",", "")

    # --- Size hints (only if Size is missing in attributes later) ---
    # Flat: 0.001" x 0.005"
    m = re.search(r"\b(\d*\.\d+)\s*\"?\s*[x×]\s*(\d*\.\d+)\s*\"?\b", text)
    if m:
        a = _try_float(m.group(1))
        b = _try_float(m.group(2))
        # heuristic: braid wire inch dimensions are typically < 0.2"
        if a is not None and b is not None and 0 < a < 0.2 and 0 < b < 0.2:
            derived["Size (parsed)"] = f'{m.group(1)}" x {m.group(2)}"'

    # Round: diameter in inches (e.g., "Round, 304V, 0.002\"", "0.002\" dia")
    # Only capture a single value when context suggests diameter.
    m = re.search(r"(?:\bround\b.*?|\bdia(?:meter)?\b.*?)(\d*\.\d+)\s*\"", text_l)
    if m:
        d = _try_float(m.group(1))
        if d is not None and 0 < d < 0.2:
            derived["Diameter (parsed)"] = f'{m.group(1)}"'

    # --- Material hint (only as a hint, do not overwrite explicit attributes) ---
    for mat in _MATERIAL_HINTS:
        if mat.lower() in text_l:
            derived["Material (parsed)"] = mat
            break

    # --- Radiopaque hint (only if explicitly stated in source text) ---
    if "radiopaque" in text_l:
        derived["Property (parsed)"] = "Radiopaque"

    return {k: v for k, v in derived.items() if _safe_str(v).strip()}


def _detect_conflicts(attrs: dict, derived: dict) -> list:
    """
    Identify obvious contradictions between structured attrs and derived free-text hints.
    This helps debug cases where the source CSV itself is inconsistent.
    """
    issues = []
    bw_type = str(attrs.get("Braid Wire Type", "") or "").lower()
    size = str(attrs.get("Size", "") or "").lower()

    derived_size = str(derived.get("Size (parsed)", "") or "").lower()
    derived_diam = str(derived.get("Diameter (parsed)", "") or "").lower()
    derived_shape = str(derived.get("Wire Shape (parsed)", "") or "").lower()

    # If attributes say round but derived shows flat dimensions
    if ("round" in bw_type or " rd" in size) and ("x" in derived_size or "×" in derived_size):
        issues.append("Attributes indicate round wire but Name/Description indicates flat size.")

    # If attributes say flat but derived indicates round diameter
    if ("flat" in bw_type or "x" in size or "×" in size) and derived_diam:
        issues.append("Attributes indicate flat wire but Name/Description indicates round diameter.")

    # If text explicitly says round/flat and attributes disagree
    if derived_shape == "round" and ("flat" in bw_type or "x" in size or "×" in size):
        issues.append("Name/Description indicates round wire but Attributes indicate flat wire.")
    if derived_shape == "flat" and ("round" in bw_type or " rd" in size):
        issues.append("Name/Description indicates flat wire but Attributes indicate round wire.")

    # If attributes say flat but derived shows round size marker
    if ("flat" in bw_type or " x " in size or "×" in size) and re.search(r"\b\d*\.?\d+\s*rd\b", derived_size):
        issues.append("Attributes indicate flat wire but Name/Description indicates round size.")

    # Tensile mismatch (min vs min) - flag only if both present and clearly different
    attr_tens = str(attrs.get("Tensile Range (ksi)", "") or "").lower()
    derived_tens = str(derived.get("Tensile Range (ksi)", "") or "").lower()
    m1 = re.search(r"(\d{3}(?:\.\d+)?)\s*ksi", attr_tens)
    m2 = re.search(r"(\d{3}(?:\.\d+)?)\s*ksi", derived_tens)
    if m1 and m2 and m1.group(1) != m2.group(1):
        issues.append(f"Tensile differs (attrs {m1.group(1)} ksi vs text {m2.group(1)} ksi).")

    return issues


def _safe_str(value) -> str:
    """Convert CSV cell to safe string (handles NaN)."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def _pick_first_column(df: pd.DataFrame, candidates) -> str:
    """Return the first existing column name from candidates (case-insensitive)."""
    if df is None or df.empty:
        return ""
    col_lookup = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in col_lookup:
            return col_lookup[key]
    return ""


def _read_csv_forgiving(input_path: Path) -> pd.DataFrame:
    """
    Read CSV with multiple encodings and normalize common problematic characters.
    Handles cases like non-breaking space (0xA0) that can break utf-8 decoding.
    """
    encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
    last_err = None
    for enc in encodings:
        try:
            df = pd.read_csv(input_path, encoding=enc)
            return df
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            # Some parse errors can still occur; keep the first non-decode error and break
            last_err = e
            break

    # Final fallback: read bytes, replace NBSP with space, then decode with replacement
    try:
        raw = Path(input_path).read_bytes()
        # Replace non-breaking spaces and other common whitespace oddities
        raw = raw.replace(b"\xa0", b" ")
        text = raw.decode("utf-8", errors="replace")
        # pandas can read from string buffer
        from io import StringIO
        return pd.read_csv(StringIO(text))
    except Exception as e:
        raise last_err or e

def load_prompts(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            logger.info("Loading prompts from %s", path)
            prompts = yaml.safe_load(f)
            # Guardrails: description prompts should almost always include attrs/orig_name
            # so the model doesn't hallucinate.
            try:
                desc_user = (prompts or {}).get("description", {}).get("user", "") or ""
                if "{attrs}" not in desc_user:
                    logger.warning(
                        "Prompt file %s: description.user does not include '{attrs}'. "
                        "Descriptions may hallucinate without product attributes.",
                        path,
                    )
            except Exception:
                # Never fail prompt loading due to guardrails
                pass
            return prompts
    except Exception as e:
        logger.error("Error loading prompts: %s", e, exc_info=True)
        raise

def process_csv_with_category(input_path, category, base_dir, row_limit=None):
    logger.info("Reading CSV input file: %s for category: %s", input_path, category)
    df = _read_csv_forgiving(Path(input_path))
    if row_limit is not None and row_limit > 0:
        df = df.head(row_limit)
        logger.info("Limiting rows processed to: %d", row_limit)

    categories = yaml.safe_load((base_dir / "categories.yaml").read_text())
    prompt_file = categories[category]["prompt_file"]
    prompts = load_prompts(base_dir / prompt_file)

    # Optional free-text columns that often contain extra specs not mapped into attributes
    input_name_col = _pick_first_column(df, candidates=["Name", "name"])
    short_desc_col = _pick_first_column(
        df,
        candidates=[
            "Short description",
            "short description",
            "Short Description",
            "Short description (html)",
            "Short description (HTML)",
        ],
    )
    long_desc_col = _pick_first_column(
        df,
        candidates=[
            "Description",
            "description",
        ],
    )
    title_output_col = _pick_first_column(df, candidates=["Title", "title", "Name", "name"]) or "Title"
    desc_output_col = long_desc_col or "Description"

    output_path = base_dir / "uploads" / "output.csv"
    checkpoint_path = base_dir / "uploads" / "qc_checkpoints.csv"
    checkpoint_interval = 50
    fsync_interval = 50
    logger.info("Writing output CSV incrementally to %s", output_path)

    fieldnames = [str(c) for c in df.columns]
    for output_col in (title_output_col, desc_output_col):
        if output_col not in fieldnames:
            fieldnames.append(output_col)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_fields = [
        "checkpoint_index",
        "rows_processed",
        "title_pass_count",
        "title_fail_count",
        "description_pass_count",
        "description_fail_count",
        "title_rewritten_count",
        "description_rewritten_count",
        "title_pass_rate",
        "description_pass_rate",
        "top_title_issues",
        "top_description_issues",
    ]
    with checkpoint_path.open("w", encoding="utf-8", newline="") as f_ck:
        ck_writer = csv_module.DictWriter(f_ck, fieldnames=checkpoint_fields, extrasaction="ignore")
        ck_writer.writeheader()
        f_ck.flush()
        try:
            os.fsync(f_ck.fileno())
        except Exception:
            pass

    title_pass_count = 0
    title_fail_count = 0
    desc_pass_count = 0
    desc_fail_count = 0
    title_rewritten_count = 0
    desc_rewritten_count = 0
    title_issue_counter: Counter = Counter()
    desc_issue_counter: Counter = Counter()
    rows_processed = 0

    def _write_checkpoint():
        nonlocal rows_processed
        if rows_processed <= 0:
            return
        title_pass_rate = round((title_pass_count / rows_processed) * 100.0, 2)
        desc_pass_rate = round((desc_pass_count / rows_processed) * 100.0, 2)
        row = {
            "checkpoint_index": (rows_processed // checkpoint_interval) if rows_processed % checkpoint_interval == 0 else (rows_processed // checkpoint_interval) + 1,
            "rows_processed": rows_processed,
            "title_pass_count": title_pass_count,
            "title_fail_count": title_fail_count,
            "description_pass_count": desc_pass_count,
            "description_fail_count": desc_fail_count,
            "title_rewritten_count": title_rewritten_count,
            "description_rewritten_count": desc_rewritten_count,
            "title_pass_rate": title_pass_rate,
            "description_pass_rate": desc_pass_rate,
            "top_title_issues": json.dumps(title_issue_counter.most_common(5), ensure_ascii=False),
            "top_description_issues": json.dumps(desc_issue_counter.most_common(5), ensure_ascii=False),
        }
        with checkpoint_path.open("a", encoding="utf-8", newline="") as f_ck:
            ck_writer = csv_module.DictWriter(f_ck, fieldnames=checkpoint_fields, extrasaction="ignore")
            ck_writer.writerow(row)
            f_ck.flush()
            try:
                os.fsync(f_ck.fileno())
            except Exception:
                pass

    # Write header immediately so UIs can start polling the file.
    with output_path.open("w", encoding="utf-8", newline="") as f_out:
        writer = csv_module.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        f_out.flush()
        try:
            os.fsync(f_out.fileno())
        except Exception:
            pass

        for idx, row in df.iterrows():
            logger.info("Processing row %d (SKU: %s)", idx, row.get("SKU", ""))
            attrs = {}
            for i in range(1, 50):
                attr_name_col = f"Attribute {i} name"
                attr_val_col  = f"Attribute {i} value(s)"
                if attr_name_col in df and attr_val_col in df:
                    raw_key = row.get(attr_name_col, "")
                    raw_val = row.get(attr_val_col, "")
                    # Skip if NaN or empty
                    if pd.isna(raw_key) or pd.isna(raw_val):
                        continue
                    key = str(raw_key).strip()
                    val = str(raw_val).strip()
                    # Skip "nan" strings and empty values
                    if key and val and key.lower() != "nan" and val.lower() != "nan":
                        attrs[key] = val

            source_name = _safe_str(row.get(input_name_col, "")) if input_name_col else _safe_str(row.get("Name", ""))
            source_short_description = _safe_str(row.get(short_desc_col, "")) if short_desc_col else ""
            source_description = _safe_str(row.get(long_desc_col, "")) if long_desc_col else ""

            # Extract additional structured hints from free-text fields (DO NOT merge into attrs;
            # keep separate to avoid mixing inconsistent source data).
            derived = _extract_attributes_from_free_text(source_name, source_short_description, source_description)
            conflicts = _detect_conflicts(attrs, derived)

            product = {
                "SKU": _normalize_sku(row.get("SKU", "")),
                "Name": source_name,
                # Pass through raw text columns so prompts can use them when attributes are sparse.
                "ShortDescription": source_short_description,
                "Description": source_description,
                "derived_attributes": derived,
                "data_conflicts": conflicts,
                "attributes": attrs,
            }
            title, title_review = call_title_llm(product, prompts, category=category)
            desc, certainty, desc_review = call_description_llm(product, prompts, title=title, category=category)
            logger.info(
                "Enriched row %d: title='%s', certainty=%.2f, title_review_passed=%s, desc_review_passed=%s",
                idx,
                title,
                certainty,
                bool((title_review or {}).get("passed", False)),
                bool((desc_review or {}).get("passed", False)),
            )

            row_out = {str(col): _safe_str(row.get(col, "")) for col in df.columns}
            row_out[title_output_col] = title
            row_out[desc_output_col] = desc

            writer.writerow(row_out)
            f_out.flush()

            rows_processed += 1
            if rows_processed % fsync_interval == 0:
                try:
                    os.fsync(f_out.fileno())
                except Exception:
                    pass
            t_pass = bool((title_review or {}).get("passed", False))
            d_pass = bool((desc_review or {}).get("passed", False))
            t_rewritten = bool((title_review or {}).get("rewritten", False))
            d_rewritten = bool((desc_review or {}).get("rewritten", False))
            t_issues = (title_review or {}).get("issues", []) or []
            d_issues = (desc_review or {}).get("issues", []) or []

            title_pass_count += 1 if t_pass else 0
            title_fail_count += 0 if t_pass else 1
            desc_pass_count += 1 if d_pass else 0
            desc_fail_count += 0 if d_pass else 1
            title_rewritten_count += 1 if t_rewritten else 0
            desc_rewritten_count += 1 if d_rewritten else 0
            title_issue_counter.update(str(x) for x in t_issues)
            desc_issue_counter.update(str(x) for x in d_issues)

            if rows_processed % checkpoint_interval == 0:
                _write_checkpoint()

        # Final partial checkpoint (if last batch < interval)
        if rows_processed % checkpoint_interval != 0:
            _write_checkpoint()
        try:
            os.fsync(f_out.fileno())
        except Exception:
            pass

    return output_path
