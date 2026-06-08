"""
Deterministic grounding checks: every numeric token in copy must come from
attribute values (exact literal substring), preventing rounding/hallucination.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set


def numeric_literals_from_attr_values(attrs: Dict[str, Any]) -> Set[str]:
    """All numeric substrings as they appear in attribute values (source of truth)."""
    allowed: Set[str] = set()
    for v in (attrs or {}).values():
        s = str(v).strip()
        if not s or s.lower() == "nan":
            continue
        for m in re.finditer(r"\d+\.\d+|\d+", s):
            allowed.add(m.group(0))
    return allowed


def find_ungrounded_numeric_literals(attrs: Dict[str, Any], text: str) -> List[str]:
    """
    Return numeric tokens in text that are not exact literals from any attribute value.
    Intentionally ignores integers of 1 digit (reduces false positives on 'a 5 mm' etc.)
    except when part of x.x pattern.
    """
    if not text:
        return []
    allowed = numeric_literals_from_attr_values(attrs or {})
    if not allowed:
        return []

    offenders: List[str] = []
    seen = set()

    # Decimals (primary spec leak vector)
    for m in re.finditer(r"\d+\.\d+", str(text)):
        raw = m.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        if raw not in allowed:
            offenders.append(raw)

    # Multi-digit integers (e.g. 20 length, 72 in 72D) — skip tails of decimals (e.g. 016 in 0.016)
    text_s = str(text)
    for m in re.finditer(r"\b\d{2,}\b", text_s):
        raw = m.group(0)
        if raw in seen:
            continue
        start = m.start()
        if start > 0 and text_s[start - 1] == ".":
            continue
        seen.add(raw)
        if raw not in allowed:
            offenders.append(raw)

    return offenders


def _norm_title_text(title: str) -> str:
    return str(title or "").replace("″", '"').lower()


def _first_attr_by_key_patterns(attrs: Dict[str, Any], patterns: List[re.Pattern]) -> str:
    """First non-empty value whose key matches any pattern (order = priority)."""
    for pat in patterns:
        for k, v in (attrs or {}).items():
            if not pat.search(str(k)):
                continue
            s = str(v).strip()
            if s and s.lower() != "nan":
                return s
    return ""


def extract_polymer_specs(attrs: Dict[str, Any]) -> Dict[str, str]:
    """
    Best-effort mapping for common WooCommerce polymer tubing exports.
    Keys are optional; missing keys yield empty strings.
    """
    out: Dict[str, str] = {}
    out["id"] = _first_attr_by_key_patterns(
        attrs,
        [
            re.compile(r"id\s*\(\s*in\s*\)", re.I),
            re.compile(r"\bi\.?\s*d\.?\s*\(\s*in\s*\)", re.I),
            re.compile(r"inner\s*diameter", re.I),
            re.compile(r"inside\s*diameter", re.I),
            re.compile(r"^id$", re.I),
        ],
    )
    out["od"] = _first_attr_by_key_patterns(
        attrs,
        [
            re.compile(r"od\s*\(\s*in\s*\)", re.I),
            re.compile(r"\bo\.?\s*d\.?\s*\(\s*in\s*\)", re.I),
            re.compile(r"outer\s*diameter", re.I),
            re.compile(r"outside\s*diameter", re.I),
            re.compile(r"^od$", re.I),
        ],
    )
    out["length"] = _first_attr_by_key_patterns(
        attrs,
        [
            re.compile(r"length\s*\(\s*in\s*\)", re.I),
            re.compile(r"\blength\b", re.I),
            re.compile(r"stock\s*length", re.I),
        ],
    )
    out["wall"] = _first_attr_by_key_patterns(
        attrs,
        [
            re.compile(r"wall\s*thickness", re.I),
            re.compile(r"\bwall\b", re.I),
        ],
    )
    out["material_grade"] = _first_attr_by_key_patterns(
        attrs,
        [
            re.compile(r"material\s*grade", re.I),
            re.compile(r"\bgrade\b", re.I),
        ],
    )
    if not out["material_grade"]:
        out["material_grade"] = _first_attr_by_key_patterns(
            attrs,
            [
                re.compile(r"^material$", re.I),
                re.compile(r"\bmaterial\b", re.I),
                re.compile(r"\bpolymer\b", re.I),
                re.compile(r"\bresin\b", re.I),
            ],
        )
    out["tubing_type"] = _first_attr_by_key_patterns(
        attrs,
        [
            re.compile(r"tubing\s*type", re.I),
            re.compile(r"\blumen\b", re.I),
            re.compile(r"^type$", re.I),
        ],
    )
    out["packaging"] = _first_attr_by_key_patterns(
        attrs,
        [
            re.compile(r"packaging", re.I),
            re.compile(r"package", re.I),
            re.compile(r"pieces\s*per", re.I),
        ],
    )
    out["french"] = _first_attr_by_key_patterns(
        attrs,
        [re.compile(r"french", re.I), re.compile(r"\bfr\b", re.I)],
    )
    return {k: v for k, v in out.items() if v}


def _must_substring(hay: str, needle: str, label: str) -> List[str]:
    if not needle:
        return []
    n = str(needle).strip()
    if not n:
        return []
    h = _norm_title_text(hay)
    # Allow common quote variants on inches in title
    needles = {n.lower(), n.lower().replace('"', "″")}
    for variant in list(needles):
        if variant.endswith("in") and re.search(r"\d", variant):
            needles.add(variant.replace("in", '"'))
    if any(v in h for v in needles if v):
        return []
    return [f"title_missing_{label}:{n}"]


def validate_polymer_title(attrs: Dict[str, Any], title: str) -> List[str]:
    """Structural checks: key specs from attributes must appear in the title."""
    issues: List[str] = []
    specs = extract_polymer_specs(attrs)
    t = title or ""

    # Wall is usually omitted from the Chamfr title template; do not require it in the title.
    for key in ("id", "od", "length"):
        val = specs.get(key, "")
        if not val:
            continue
        m = re.search(r"\d+\.\d+|\d+", val)
        if m:
            num = m.group(0)
            if num not in t.replace("″", '"'):
                issues.append(f"title_missing_{key}_number:{num}")

    if specs.get("material_grade"):
        issues.extend(_must_substring(t, specs["material_grade"], "material_grade"))
    if specs.get("tubing_type"):
        issues.extend(_must_substring(t, specs["tubing_type"], "tubing_type"))
    if specs.get("packaging"):
        # e.g. "5/Bag" -> require "5" and "bag" or full string
        p = specs["packaging"]
        if p.lower() not in _norm_title_text(t):
            if not (re.search(r"\d+", p) and re.search(r"bag|vial|coil|order", t, re.I)):
                issues.append(f"title_missing_packaging:{p}")

    issues.extend(find_ungrounded_numeric_literals(attrs, t))
    return issues


def validate_polymer_description(attrs: Dict[str, Any], description: str) -> List[str]:
    """
    Descriptions must not introduce numeric literals that are not present verbatim
    in attribute values (prevents 0.010 vs 0.01 wall drift).
    """
    return find_ungrounded_numeric_literals(attrs or {}, description or "")
