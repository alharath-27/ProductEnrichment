import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from .attribute_grounding import (
    find_ungrounded_numeric_literals,
    numeric_literals_from_attr_values,
    validate_polymer_description,
    validate_polymer_title,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.deepseek.com/v1/chat/completions"
if load_dotenv:
    load_dotenv()


def _get_api_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "").strip()


def _headers() -> Dict[str, str]:
    api_key = _get_api_key()
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _qa_mode() -> str:
    """standard saves LLM calls; strict preserves the heavier review behavior."""
    mode = os.getenv("QA_MODE", "standard").strip().lower()
    return mode if mode in {"standard", "strict"} else "standard"


def _strict_qa() -> bool:
    return _qa_mode() == "strict"


REVIEW_SYSTEM_PROMPT = """You are a strict QA reviewer for SKU-specific ecommerce content.

Goal:
- Ensure outputs for one SKU NEVER contain details from any other SKU.
- Ensure any specific attribute values mentioned are supported by the provided Attributes JSON.

Rules:
- Attributes JSON is the source of truth for ALL specific specs (materials, sizes, quantities, durometer, additives, finishes, tolerances, etc.).
- Generic statements about use-cases are allowed only if they do not add new, specific specs.
- If SOURCE FIELDS are provided, they may be used to validate high-level use-cases only (still not a source of specs).
- If the candidate includes a highly specific clinical procedure, indication, or performance claim not supported by Attributes/SOURCE FIELDS, generalize it to a safer, non-committal use-case sentence.
- If anything is unsupported or looks like cross-SKU mixing, you MUST rewrite the text to remove unsupported content while keeping the same overall structure and intent.

Output JSON ONLY."""

TITLE_CHECK_SYSTEM_PROMPT = """You are a strict template-readiness checker for ecommerce title generation.

Your ONLY job is to analyze whether the provided product Attributes can fill the category's Title TEMPLATE.
You MUST NOT invent values. Do not write the final title.

Return JSON ONLY with this schema:
{
  "template_ready": true,
  "missing_requirements": ["..."],
  "suggested_omissions": ["..."],
  "notes": ["..."]
}

Guidance:
- Missing requirements should refer to required template segments or required attribute concepts (e.g., ID, OD, Length, Material, Tubing Type).
- If a segment can be safely omitted according to the template/rules, put it in suggested_omissions.
- If the rules allow a fallback (e.g., brand from old_name), describe it in notes; do not fabricate brand/specs.
"""


def _require_api_key():
    if not _get_api_key():
        raise ValueError("DEEPSEEK_API_KEY environment variable not set")

def _truncate(text: str, max_chars: int = 1800) -> str:
    if not text:
        return ""
    t = str(text)
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 3].rstrip() + "..."


def _build_source_fields_block(product: dict) -> str:
    """
    Provide extra CSV context (Name/Description) to reduce hallucinations
    when attributes are sparse or inconsistent across categories.
    """
    name = _truncate(product.get("Name", "") or "", 300)
    short_desc = _truncate(product.get("ShortDescription", "") or "", 900)
    desc = _truncate(product.get("Description", "") or "", 1800)
    if not name and not short_desc and not desc:
        return ""
    return (
        "\n\nSOURCE FIELDS (from input CSV):\n"
        f"Name: {name}\n"
        f"Short description: {short_desc}\n"
        f"Description: {desc}\n"
    )

def _build_derived_features_block(product: dict) -> str:
    derived = product.get("derived_attributes") or {}
    if not derived:
        return ""
    try:
        payload = json.dumps(derived, indent=2, ensure_ascii=False)
    except Exception:
        payload = str(derived)
    payload = _truncate(payload, 1800)
    return "\nDERIVED FEATURES (parsed from Name/Description; use ONLY to fill missing attrs):\n" + payload + "\n"

def _build_conflict_warning_block(product: dict) -> str:
    conflicts = product.get("data_conflicts") or []
    if not conflicts:
        return ""
    # Hard guardrail: specs must not come from free text when conflicts exist.
    lines = "\n".join(f"- {c}" for c in conflicts)
    return (
        "\nCONFLICT WARNING:\n"
        "Attributes are the source of truth for ALL specs (wire shape/size/tensile/packaging).\n"
        "The input CSV Name/Description contains conflicting spec cues. Do NOT use the source text or derived features for specs.\n"
        "You may use the source text ONLY for safe application context wording.\n"
        f"{lines}\n"
    )

def normalize_wp_units(text: str) -> str:
    """
    Normalize typography so WooCommerce/WordPress reliably displays inch marks.

    WordPress is UTF-8, but imports/exports/plugins sometimes mishandle “smart” quotes
    and prime symbols. We standardize to ASCII quotes:
      - inches:  ″ (U+2033) -> "
      - curly quotes: “ ” -> "
      - feet:   ′ (U+2032) -> '  (safe, optional)
      - NBSP:   \\u00A0 -> space
    """
    if not text:
        return text

    t = str(text)
    # Non-breaking space -> regular space
    t = t.replace("\u00A0", " ")

    # Common WordPress/HTML entities (leave as plain symbols for reliable rendering)
    t = (
        t.replace("&trade;", "™")
         .replace("&reg;", "®")
         .replace("&copy;", "©")
    )

    # Convert common textual trademark patterns
    # (Keep UTF-8 symbols; WP renders these well)
    t = re.sub(r"\(\s*tm\s*\)", "™", t, flags=re.IGNORECASE)
    t = re.sub(r"\(\s*r\s*\)", "®", t, flags=re.IGNORECASE)
    t = re.sub(r"\(\s*c\s*\)", "©", t, flags=re.IGNORECASE)

    # Double-prime (inches) and smart double quotes -> ASCII double quote
    t = (
        t.replace("\u2033", '"')  # DOUBLE PRIME
         .replace("\u201C", '"')  # LEFT DOUBLE QUOTATION MARK
         .replace("\u201D", '"')  # RIGHT DOUBLE QUOTATION MARK
         .replace("\u201E", '"')  # DOUBLE LOW-9 QUOTATION MARK
         .replace("\u00AB", '"')  # «
         .replace("\u00BB", '"')  # »
    )

    # Prime (feet) and smart single quotes -> ASCII apostrophe
    t = (
        t.replace("\u2032", "'")  # PRIME
         .replace("\u2018", "'")  # LEFT SINGLE QUOTATION MARK
         .replace("\u2019", "'")  # RIGHT SINGLE QUOTATION MARK
         .replace("\u201B", "'")  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    )

    # Collapse weird whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_dimension_label_order(text: str) -> str:
    """
    Ensure dimension labels come after the numeric+unit token.

    Examples:
      - 'ID 0.177"'  -> '0.177" ID'
      - 'OD: 0.092 in' -> '0.092 in OD'
      - 'Wall 0.005" Wall' -> '0.005" Wall'
    """
    if not text:
        return text

    t = str(text)

    label_pat = r"(?:I\.?D\.?|O\.?D\.?|ID|OD|Wall|Length)"
    value_pat = r"(?:\d*\.\d+|\d+(?:\.\d+)?|\d+\s+\d+/\d+|\d+/\d+)"

    def _label_display(label_raw: str) -> str:
        key = str(label_raw).replace(".", "").upper()
        return {
            "ID": "ID",
            "OD": "OD",
            "WALL": "Wall",
            "LENGTH": "Length",
        }.get(key, str(label_raw))

    def _repl(m: re.Match) -> str:
        label_raw = m.group("label")
        value = m.group("value")
        unit = m.group("unit")
        label = _label_display(label_raw)
        if unit in ('"', "″"):
            return f'{value}" {label}'
        return f"{value} {unit} {label}"

    # Fix inch-mark units (e.g., 0.177")
    t = re.sub(
        rf"\b(?P<label>{label_pat})\b\s*[:=]?\s*(?P<value>{value_pat})\s*(?P<unit>[\"″])\s*(?:(?P=label)\b)?",
        _repl,
        t,
        flags=re.IGNORECASE,
    )

    # Fix word units (e.g., 0.177 in, 4 mm)
    t = re.sub(
        rf"\b(?P<label>{label_pat})\b\s*[:=]?\s*(?P<value>{value_pat})\s*(?P<unit>mm|cm|in(?:ches)?|inch)\b\s*(?:(?P=label)\b)?",
        _repl,
        t,
        flags=re.IGNORECASE,
    )

    # Guard against accidental duplicates like "0.177\" ID ID"
    t = re.sub(r"\b(ID|OD|Wall|Length)\s+\1\b", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def normalize_copy_style(text: str, *, kind: str = "description") -> str:
    """
    Deterministic post-style normalization for consistent WP output.
    Keeps numeric literals intact while standardizing typography and unit phrasing.
    """
    if not text:
        return text

    t = str(text)
    t = t.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")

    # Normalize lead-time phrasing variants to a consistent token.
    t = re.sub(
        r"\b(\d+)\s*(?:to|-)\s*(\d+)\s*business\s*days\b",
        r"\1-\2 business days",
        t,
        flags=re.IGNORECASE,
    )

    if kind == "description":
        # Standardize inch units in descriptions to inch mark style.
        t = re.sub(r"(\d+(?:\.\d+)?)\s*inches\b", r'\1"', t, flags=re.IGNORECASE)
        t = re.sub(r"(\d+(?:\.\d+)?)\s*inch\b", r'\1"', t, flags=re.IGNORECASE)
        t = re.sub(r"(\d+(?:\.\d+)?)\s*in\.(?=\s|$)", r'\1"', t, flags=re.IGNORECASE)
        t = re.sub(r"(\d+(?:\.\d+)?)\s*in\b", r'\1"', t, flags=re.IGNORECASE)

    t = re.sub(r"\s+", " ", t).strip()
    return t

def repair_json(raw):
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning("repair_json: failed initial parse: %s", e)
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception as ee:
                logger.warning("repair_json: failed fallback parse: %s", ee)
                return None
    return None

def check_title_template_readiness(
    *,
    category: str,
    sku: str,
    old_name: str,
    attrs: Dict[str, Any],
    title_prompt_user: str,
) -> Dict[str, Any]:
    """
    Lightweight LLM check to decide whether the title template can be filled from attrs.
    This does NOT generate the title; it returns structured guidance.
    """
    _require_api_key()
    user_prompt = (
        f"Category: {category}\n"
        f"SKU: {sku}\n"
        f"old_name: {old_name}\n\n"
        "TITLE TEMPLATE & RULES (verbatim):\n"
        f"{title_prompt_user.strip()}\n\n"
        "ATTRIBUTES JSON:\n"
        f"{json.dumps(attrs or {}, indent=2, ensure_ascii=False)}\n\n"
        "Return JSON only per the required schema."
    )
    try:
        res = requests.post(
            API_URL,
            headers=_headers(),
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": TITLE_CHECK_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
            },
        )
        res.raise_for_status()
        raw = res.json()["choices"][0]["message"]["content"]
        parsed = repair_json(raw)
        if not isinstance(parsed, dict):
            return {
                "template_ready": True,
                "missing_requirements": [],
                "suggested_omissions": [],
                "notes": ["template_readiness_parse_error"],
            }
        parsed.setdefault("template_ready", True)
        parsed.setdefault("missing_requirements", [])
        parsed.setdefault("suggested_omissions", [])
        parsed.setdefault("notes", [])
        return parsed
    except Exception as e:
        return {
            "template_ready": True,
            "missing_requirements": [],
            "suggested_omissions": [],
            "notes": ["template_readiness_api_error", str(e)],
        }


def _split_attr_values(value: Any) -> List[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    parts = re.split(r"\s*\|\s*|\s*;\s*", text)
    out: List[str] = []
    for p in parts:
        p2 = str(p).strip()
        if p2 and p2.lower() != "nan":
            out.append(p2)
    # keep full string too (often contains meaningful formatting)
    if text not in out:
        out.append(text)
    # de-dupe, keep order
    seen = set()
    deduped: List[str] = []
    for item in out:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _allowed_literals_from_attrs(attrs: Dict[str, Any]) -> List[str]:
    allowed: List[str] = []
    for v in (attrs or {}).values():
        allowed.extend(_split_attr_values(v))
    # Keep it bounded to avoid huge review prompts
    allowed = [a for a in allowed if a and len(a) <= 120]
    return allowed[:120]


def _review_text_with_llm(
    *,
    sku: str,
    attrs: Dict[str, Any],
    kind: str,
    candidate_text: str,
    constraints_hint: str = "",
    source_fields: str = "",
) -> Dict[str, Any]:
    """
    LLM-based review gate to prevent cross-SKU mixing and unsupported attribute mentions.
    Returns dict with keys: pass (bool), issues (list[str]), fixed_text (str).
    """
    _require_api_key()

    allowed_literals = _allowed_literals_from_attrs(attrs)
    user_prompt = (
        f"Review the following {kind} for SKU: {sku}\n\n"
        f"Constraints (keep if possible):\n{constraints_hint.strip() or '(none)'}\n\n"
        "Attributes JSON (source of truth for specs):\n"
        f"{json.dumps(attrs or {}, indent=2, ensure_ascii=False)}\n\n"
        "Allowed literal values (appear in Attributes; helpful for checking):\n"
        f"{json.dumps(allowed_literals, ensure_ascii=False)}\n\n"
        f"Candidate {kind}:\n{candidate_text}\n\n"
        "Return JSON ONLY in this format:\n"
        '{\n  "pass": true,\n  "issues": ["..."],\n  "fixed_text": "..." \n}\n\n'
        "If it fails, set pass=false and provide fixed_text rewritten to comply."
    )
    if source_fields and str(source_fields).strip():
        user_prompt += "\n\n" + str(source_fields).strip()

    res = requests.post(
        API_URL,
        headers=_headers(),
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
        },
    )
    logger.info("Review LLM response status: %d", res.status_code)
    res.raise_for_status()
    raw = res.json()["choices"][0]["message"]["content"]
    parsed = repair_json(raw)
    if not isinstance(parsed, dict):
        return {"pass": False, "issues": ["review_parse_error"], "fixed_text": candidate_text}
    parsed.setdefault("issues", [])
    if "fixed_text" not in parsed or not str(parsed.get("fixed_text") or "").strip():
        parsed["fixed_text"] = candidate_text
    if "pass" not in parsed:
        parsed["pass"] = False
    return parsed


def _hard_validate_against_attrs(attrs: Dict[str, Any], text: str) -> List[str]:
    """
    Deterministic backstop: decimals and multi-digit integers must match literals from
    attribute values (no rounding drift). Additional heuristics for %, durometer, and
    unit-suffixed numbers when the bare value exists in the attributes JSON.
    """
    if not text:
        return []
    attrs_blob = json.dumps(attrs or {}, ensure_ascii=False).lower()
    t = str(text)

    offenders: List[str] = list(find_ungrounded_numeric_literals(attrs or {}, t))

    attr_keys_l = [str(k).lower() for k in (attrs or {}).keys()]
    has_mm_attrs = any("(mm)" in k or k.endswith(" mm") or " mm" in k for k in attr_keys_l)
    has_in_attrs = any("(in)" in k or k.endswith(" in") or " inch" in k for k in attr_keys_l)
    has_psi_attrs = any("(psi)" in k or " psi" in k for k in attr_keys_l)
    has_cm_values = "cm" in attrs_blob

    patterns = [
        r"\b\d+(?:\.\d+)?\s*%\b",
        r"\b\d{2,3}\s*[dD]\b",
        r"\b\d+(?:\.\d+)?\s*(?:ksi|mpa|gpa|mm|cm|in|inch|inches)\b",
        r"\b\d*\.\d+\s*(?:\"|″)\b",
    ]

    for pat in patterns:
        for m in re.finditer(pat, t, flags=re.IGNORECASE):
            token = m.group(0).strip()
            if not token:
                continue
            token_l = token.lower()
            if token_l in offenders:
                continue
            if token_l in attrs_blob:
                continue

            inch_m = re.match(r"^\s*(\d*\.\d+)\s*(?:\"|″)\s*$", token_l)
            if inch_m and has_in_attrs:
                num = inch_m.group(1)
                if num and num in attrs_blob:
                    continue

            mm_m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*mm\s*$", token_l)
            if mm_m and has_mm_attrs:
                num = mm_m.group(1)
                if num:
                    if num in attrs_blob:
                        continue
                    num_compact = num.rstrip("0").rstrip(".")
                    if num_compact and num_compact in attrs_blob:
                        continue

            cm_m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*cm\s*$", token_l)
            if cm_m and has_cm_values:
                num = cm_m.group(1)
                if num:
                    if num in attrs_blob:
                        continue
                    num_compact = num.rstrip("0").rstrip(".")
                    if num_compact and num_compact in attrs_blob:
                        continue

            psi_m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*psi\s*$", token_l)
            if psi_m and has_psi_attrs:
                num = psi_m.group(1)
                if num:
                    if num in attrs_blob:
                        continue
                    num_compact = num.rstrip("0").rstrip(".")
                    if num_compact and num_compact in attrs_blob:
                        continue

            # Strip trailing unit word; if decimal core is attribute-grounded, allow (e.g. "0.021 inches")
            core_m = re.match(r"^\s*(\d+\.\d+)\s+[a-z]+\s*$", token_l)
            if core_m:
                core = core_m.group(1)
                if core and core in numeric_literals_from_attr_values(attrs or {}):
                    continue

            offenders.append(token)

    seen = set()
    deduped: List[str] = []
    for x in offenders:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(x)
    return deduped


def _attr_value(attrs: Dict[str, Any], *names: str) -> str:
    lookup = {str(k).strip().lower(): str(v).strip() for k, v in (attrs or {}).items()}
    for name in names:
        value = lookup.get(str(name).strip().lower(), "")
        if value and value.lower() != "nan":
            return value
    return ""


def _format_packaging(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    m = re.match(r"^\s*(\d+)\s*/\s*([A-Za-z]+)\s*$", text)
    if m:
        return f"{m.group(1)}/{m.group(2).capitalize()}"
    return text


def _clean_title_part(value: str) -> str:
    text = normalize_copy_style(normalize_dimension_label_order(normalize_wp_units(value or "")), kind="title")
    return text.strip(" ,-–")


def _join_title_parts(parts: List[str]) -> str:
    cleaned = [_clean_title_part(p) for p in parts if _clean_title_part(p)]
    return " – ".join(cleaned)


def _deterministic_needles_title(product: Dict[str, Any]) -> str:
    """Build consistent Needles & Scalpels titles from attributes."""
    attrs = product.get("attributes") or {}
    product_type = _attr_value(attrs, "Needles & Scalpels Type")
    packaging = _format_packaging(_attr_value(attrs, "Packaging"))
    material = _attr_value(attrs, "Material")
    color = _attr_value(attrs, "Color")
    lead = f"{packaging}, by Chamfr" if packaging else "by Chamfr"

    product_type_l = product_type.lower()
    if "scalpel" in product_type_l:
        blade_style = _attr_value(attrs, "Blade Style")
        blade_material = _attr_value(attrs, "Blade Material")
        blade = f"{blade_style} Blade" if blade_style else ""
        materials = ", ".join(
            p for p in [material, f"{blade_material} Blade" if blade_material else ""] if p
        )
        return _join_title_parts([
            "Safety Scalpel" if "safety" in (product.get("Name", "") or "").lower() else "Scalpel",
            blade,
            color,
            materials,
            lead,
        ])

    if "suture" in product_type_l:
        diameter = _attr_value(attrs, "Diameter (mm)")
        length = _attr_value(attrs, "Length (mm)")
        point_type = _attr_value(attrs, "Point Type")
        curvature = _attr_value(attrs, "Curvature")
        hole = _attr_value(attrs, "Thread Hole Diameter (mm)")
        coating = _attr_value(attrs, "Coating")
        size = ", ".join(
            p
            for p in [
                f"{diameter} mm Diameter" if diameter else "",
                f"{length} mm Length" if length else "",
            ]
            if p
        )
        geometry = ", ".join(
            p
            for p in [
                point_type,
                f"{curvature} Curvature" if curvature else "",
                f"{hole} mm Hole" if hole else "",
            ]
            if p
        )
        material_section = ", ".join(
            p for p in [material, f"{coating} Coating" if coating else ""] if p
        )
        return _join_title_parts(["Suture Needle", size, geometry, material_section, lead])

    gauge = _attr_value(attrs, "Gauge")
    wall = _attr_value(attrs, "Wall Thickness")
    cannula_length = _attr_value(attrs, "Cannula Length")
    subtype = product_type or "Needle"
    size = ", ".join(
        p
        for p in [
            f"{gauge} Gauge" if gauge else "",
            wall,
            f"{cannula_length} Length" if cannula_length else "",
        ]
        if p
    )
    details = ", ".join(p for p in [material, color] if p)
    return _join_title_parts([subtype, size, details, lead])


def deterministic_title_for_category(product: Dict[str, Any], category: str) -> Optional[str]:
    if category == "needles":
        title = _deterministic_needles_title(product)
        return title if title and "by Chamfr" in title else None
    return None


def call_title_llm(product, prompts, category: str = ""):
    logger.info("LLM: Formatting title for SKU: %s", product["SKU"])
    _require_api_key()
    deterministic_title = deterministic_title_for_category(product, category)
    if deterministic_title:
        issues = _hard_validate_against_attrs(product.get("attributes") or {}, deterministic_title)
        return deterministic_title, {
            "passed": not issues,
            "rewritten": False,
            "issues": issues,
        }

    source_block = _build_source_fields_block(product)
    derived_block = _build_derived_features_block(product)
    conflict_block = _build_conflict_warning_block(product)
    title_user_prompt = prompts["title"]["user"]
    strict = _strict_qa()
    readiness = None
    if strict:
        readiness = check_title_template_readiness(
            category=str(category or ""),
            sku=str(product.get("SKU", "") or ""),
            old_name=str(product.get("Name", "") or ""),
            attrs=product.get("attributes") or {},
            title_prompt_user=title_user_prompt,
        )
    readiness_note = ""
    try:
        if readiness and (readiness.get("missing_requirements") or readiness.get("suggested_omissions")):
            missing = readiness.get("missing_requirements") or []
            omits = readiness.get("suggested_omissions") or []
            notes = readiness.get("notes") or []
            readiness_note = (
                "\n\nTEMPLATE READINESS CHECK (use this guidance; do not invent values):\n"
                + (("- Missing requirements: " + ", ".join(str(x) for x in missing) + "\n") if missing else "")
                + (("- Suggested omissions: " + ", ".join(str(x) for x in omits) + "\n") if omits else "")
                + (("- Notes: " + " | ".join(str(x) for x in notes) + "\n") if notes else "")
            )
    except Exception:
        readiness_note = ""

    user_base = (
        prompts["title"]["user"].format(
            sku=product["SKU"],
            orig_name=product["Name"],
            orig_description=product.get("Description", ""),
            attrs=json.dumps(product["attributes"], indent=2),
        )
        + source_block
        + derived_block
        + conflict_block
        + (
            "\n\nTITLE CONSISTENCY REQUIREMENT:\n"
            "- Follow the category Title TEMPLATE exactly: same slot order, same separators, same ending rules.\n"
            "- Do not vary title structure for style. Only omit a template segment when the category rules allow it and the data is missing.\n"
            "- The title should be consistent with other products in this same selected category.\n"
        )
        + readiness_note
    )
    temp = 0.0
    max_attempts = 4 if category == "polymertubing" else 1
    last_issues: List[str] = []

    try:
        fixed_title = ""
        review: Dict[str, Any] = {"pass": True, "issues": []}
        initial_title = ""

        for attempt in range(max_attempts):
            extra = ""
            if category == "polymertubing" and last_issues:
                extra = (
                    "\n\nREGENERATION (previous title failed automated validation):\n"
                    + "\n".join(f"- {i}" for i in last_issues[:12])
                    + "\nRegenerate the title using ONLY attribute literals for numbers; do not round or reformat decimals."
                )
            messages = [
                {"role": "system", "content": prompts["title"]["system"]},
                {"role": "user", "content": user_base + extra},
            ]
            res = requests.post(
                API_URL,
                headers=_headers(),
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "temperature": temp,
                },
            )
            logger.info("Deepseek response status: %d", res.status_code)
            logger.debug("Deepseek response: %s", res.text)
            raw = res.json()["choices"][0]["message"]["content"]
            parsed = repair_json(raw)
            if not isinstance(parsed, dict):
                logger.warning("Title LLM: No valid JSON found in response.")
                return "[PARSE ERROR]", {"passed": False, "rewritten": False, "issues": ["title_json_missing"]}
            title = str(parsed.get("title", "") or "")
            title = normalize_copy_style(
                normalize_dimension_label_order(normalize_wp_units(title)),
                kind="title",
            )
            if attempt == 0:
                initial_title = title

            hard_issues = _hard_validate_against_attrs(product.get("attributes") or {}, title)
            polymer_issues: List[str] = []
            if category == "polymertubing":
                polymer_issues = validate_polymer_title(product.get("attributes") or {}, title)

            issues_to_fix = list(dict.fromkeys(list(hard_issues) + list(polymer_issues)))
            fixed_title = title
            review = {
                "pass": not issues_to_fix,
                "issues": (["polymer_title_grounding_failed"] if polymer_issues else []) + issues_to_fix,
            }

            constraints = (
                "Enforce title template consistency. The title must follow the selected category's "
                "Title TEMPLATE exactly: same slot order, same separator style, same ending rules. "
                "Do not vary title structure for style. Do not invent values."
                "\n\nCATEGORY TITLE TEMPLATE AND RULES:\n"
                + title_user_prompt.strip()
            )
            if issues_to_fix:
                constraints = (
                    constraints
                    + "\n\nRemove/replace unsupported spec tokens: "
                    + ", ".join(issues_to_fix)
                    + ". Use ONLY attribute literals for numbers; do not round or reformat decimals."
                )

            # Titles must be consistent across a category, so always run this review.
            # Description review remains conditional so descriptions can vary and batches stay cheaper.
            review = _review_text_with_llm(
                sku=str(product.get("SKU", "") or ""),
                attrs=product.get("attributes") or {},
                kind="title",
                candidate_text=title,
                constraints_hint=constraints,
            )
            fixed_title = normalize_copy_style(
                normalize_dimension_label_order(normalize_wp_units(review.get("fixed_text", title))),
                kind="title",
            )

            hard_final = _hard_validate_against_attrs(product.get("attributes") or {}, fixed_title)
            polymer_final: List[str] = []
            if category == "polymertubing":
                polymer_final = validate_polymer_title(product.get("attributes") or {}, fixed_title)

            last_issues = list(hard_final) + list(polymer_final)
            if hard_final or polymer_final:
                review["pass"] = False
                review["issues"] = (review.get("issues", []) or []) + (["polymer_title_grounding_failed"] if polymer_final else []) + last_issues

            if not last_issues or category != "polymertubing":
                break

            logger.warning(
                "Title attempt %d/%d failed validation for SKU %s: %s",
                attempt + 1,
                max_attempts,
                product.get("SKU"),
                last_issues,
            )

        rewritten = fixed_title.strip() != initial_title.strip()
        issues = list(review.get("issues", []) or [])
        try:
            if readiness and readiness.get("missing_requirements"):
                issues = ["template_readiness_missing:" + ", ".join(str(x) for x in readiness["missing_requirements"])] + issues
        except Exception:
            pass
        return fixed_title, {"passed": bool(review.get("pass", False)), "rewritten": rewritten, "issues": issues}
    except Exception as e:
        logger.error("Error calling title LLM: %s", e, exc_info=True)
        return "[API ERROR]", {"passed": False, "rewritten": False, "issues": ["title_api_error", str(e)]}

def call_description_llm(product, prompts, title="", category: str = ""):
    logger.info("LLM: Formatting description for SKU: %s", product["SKU"])
    _require_api_key()
    source_block = _build_source_fields_block(product)
    derived_block = _build_derived_features_block(product)
    conflict_block = _build_conflict_warning_block(product)
    style_hint = ""
    if category == "polymertubing":
        sku_s = str(product.get("SKU", "") or "")
        h = abs(hash(sku_s))
        openers = [
            "Vary the opening: start with the tubing construction / lumen style (no dimensions).",
            "Vary the opening: start with the base polymer family from attributes (no dimensions).",
            "Vary the opening: start with packaging or supply form from attributes (no dimensions).",
            "Vary the opening: start with secondary specs (wall / French / additives) only if present in attributes (no ID/OD/length).",
        ]
        style_hint = "\n\n" + openers[h % len(openers)]
    messages = [
        {"role": "system", "content": prompts["description"]["system"]},
        {"role": "user",   "content": (
            prompts["description"]["user"].format(
                sku=product["SKU"],
                orig_name=product["Name"],
                orig_description=product.get("Description", ""),
                attrs=json.dumps(product["attributes"], indent=2),
                title=title,
            )
            + source_block
            + derived_block
            + conflict_block
            + style_hint
        )},
    ]
    try:
        desc_temp = 0.45 if category == "polymertubing" else 0.35
        res = requests.post(API_URL, headers=_headers(), json={
            "model": "deepseek-chat",
            "messages": messages,
            # Slightly higher for polymer improves phrasing diversity; numerics are grounded afterward.
            "temperature": desc_temp
        })
        logger.info("Deepseek response status: %d", res.status_code)
        logger.debug("Deepseek response: %s", res.text)
        raw = res.json()["choices"][0]["message"]["content"]
        parsed = repair_json(raw)
        if not parsed:
            logger.warning("Description LLM: No valid JSON found in response.")
            return "[PARSE ERROR]", 0.0, {"passed": False, "rewritten": False, "issues": ["description_parse_error"]}

        desc = normalize_copy_style(
            normalize_dimension_label_order(normalize_wp_units(parsed.get("description", ""))),
            kind="description",
        )
        certainty = float(parsed.get("certainty_score", 0.7) or 0.7)

        constraints_hint = (
            "Preserve sentence count and length requirements from the prompt. "
            "Do not repeat title specs; do not add new specs not in Attributes."
        )
        try:
            desc_sys = str((prompts or {}).get("description", {}).get("system", "") or "").lower()
            desc_user = str((prompts or {}).get("description", {}).get("user", "") or "").lower()
            if "mesh/membrane" in desc_sys or "mesh/membrane" in desc_user:
                constraints_hint += (
                    " Include 1–2 high-level use cases. "
                    "Use-case context may come from SOURCE FIELDS; do not add new specs or performance claims."
                )
        except Exception:
            pass

        hard_issues = _hard_validate_against_attrs(product.get("attributes") or {}, desc)
        if category == "polymertubing":
            hard_issues = list(dict.fromkeys(hard_issues + validate_polymer_description(product.get("attributes") or {}, desc)))

        fixed_desc = desc
        review = {"pass": not hard_issues, "issues": list(hard_issues)}
        strict = _strict_qa()

        if strict or hard_issues:
            hint = constraints_hint
            if hard_issues:
                hint += (
                    " Remove/replace unsupported spec tokens: "
                    + ", ".join(hard_issues)
                    + ". Use exact numeric literals from Attributes (no rounding)."
                )

            review = _review_text_with_llm(
                sku=str(product.get("SKU", "") or ""),
                attrs=product.get("attributes") or {},
                kind="description",
                candidate_text=desc,
                constraints_hint=hint,
                source_fields=source_block,
            )
            fixed_desc = normalize_copy_style(
                normalize_dimension_label_order(normalize_wp_units(review.get("fixed_text", desc))),
                kind="description",
            )

            if strict and not bool(review.get("pass", False)) and fixed_desc.strip() != desc.strip():
                final_review = _review_text_with_llm(
                    sku=str(product.get("SKU", "") or ""),
                    attrs=product.get("attributes") or {},
                    kind="description",
                    candidate_text=fixed_desc,
                    constraints_hint=constraints_hint
                    + " This is the rewritten final description; validate this final text.",
                    source_fields=source_block,
                )
                fixed_desc = normalize_copy_style(
                    normalize_dimension_label_order(normalize_wp_units(final_review.get("fixed_text", fixed_desc))),
                    kind="description",
                )
                review = {
                    "pass": bool(final_review.get("pass", False)),
                    "issues": (review.get("issues", []) or []) + (final_review.get("issues", []) or []),
                }

        final_hard_issues = _hard_validate_against_attrs(product.get("attributes") or {}, fixed_desc)
        if category == "polymertubing":
            final_hard_issues = list(
                dict.fromkeys(final_hard_issues + validate_polymer_description(product.get("attributes") or {}, fixed_desc))
            )

        if final_hard_issues:
            if strict or category == "polymertubing":
                review2 = _review_text_with_llm(
                    sku=str(product.get("SKU", "") or ""),
                    attrs=product.get("attributes") or {},
                    kind="description",
                    candidate_text=fixed_desc,
                    constraints_hint="These literals are still not allowed: "
                    + ", ".join(final_hard_issues)
                    + ". Copy numbers exactly from Attributes JSON only.",
                    source_fields=source_block,
                )
                fixed_desc = normalize_copy_style(
                    normalize_dimension_label_order(normalize_wp_units(review2.get("fixed_text", fixed_desc))),
                    kind="description",
                )
                review = {
                    "pass": bool(review2.get("pass", False)),
                    "issues": (review.get("issues", []) or []) + ["hard_validation_failed"] + final_hard_issues + (review2.get("issues", []) or []),
                }
            else:
                review = {
                    "pass": False,
                    "issues": (review.get("issues", []) or []) + ["hard_validation_failed"] + final_hard_issues,
                }

            if category == "polymertubing":
                still_bad = validate_polymer_description(product.get("attributes") or {}, fixed_desc)
                still_hard = _hard_validate_against_attrs(product.get("attributes") or {}, fixed_desc)
                if still_bad or still_hard:
                    review3 = _review_text_with_llm(
                        sku=str(product.get("SKU", "") or ""),
                        attrs=product.get("attributes") or {},
                        kind="description",
                        candidate_text=fixed_desc,
                        constraints_hint="These literals are still not allowed: "
                        + ", ".join(still_hard + still_bad)
                        + ". Copy numbers exactly from Attributes JSON only.",
                        source_fields=source_block,
                    )
                    fixed_desc = normalize_copy_style(
                        normalize_dimension_label_order(normalize_wp_units(review3.get("fixed_text", fixed_desc))),
                        kind="description",
                    )
                    review = {
                        "pass": bool(review3.get("pass", False)),
                        "issues": (review.get("issues", []) or []) + (review3.get("issues", []) or []),
                    }

        rewritten = fixed_desc.strip() != desc.strip()
        if rewritten:
            certainty = max(0.0, min(1.0, certainty * 0.9))
        return fixed_desc, certainty, {"passed": bool(review.get("pass", False)), "rewritten": rewritten, "issues": review.get("issues", [])}
    except Exception as e:
        logger.error("Error calling description LLM: %s", e, exc_info=True)
        return "[API ERROR]", 0.0, {"passed": False, "rewritten": False, "issues": ["description_api_error", str(e)]}
