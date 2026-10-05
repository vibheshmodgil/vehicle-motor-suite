"""Deterministic first-pass scoring; unknown semantic correctness stays unverified."""
from __future__ import annotations

import re


UNIT_PATTERNS = {
    "km/h": [(r"km\s*/\s*h|kmh|kph", 1.0), (r"mph", 1.609344), (r"m\s*/\s*s", 3.6)],
    "s": [(r"seconds?|s\b", 1.0)],
    "rpm": [(r"rpm", 1.0)],
    "N m": [(r"N\s*[·.\- ]?\s*m\b|newton[ -]?met(?:er|re)s?", 1.0)],
    "N": [(r"N\b|newtons?", 1.0)],
    "kW": [(r"kW", 1.0), (r"W\b", 0.001)],
    "W": [(r"W\b", 1.0), (r"kW", 1000.0)],
    "%": [(r"%|percent", 1.0)],
    "m": [(r"m\b|met(?:er|re)s?", 1.0)],
    "m²": [(r"m\s*[²2]", 1.0)],
    "ratio": [(r":\s*1\b", 1.0)],
    "dimensionless": [(r"(?=.)", 1.0)],
}


def numerical_score(answer, expected):
    if not expected:
        return None, []
    details = []
    for target in expected:
        values = []
        for pattern, scale in UNIT_PATTERNS.get(target["unit"], []):
            rx = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:" + pattern + r")", re.I)
            values.extend(float(m.group(1)) * scale for m in rx.finditer(answer))
        if target["unit"] == "rpm":
            values.extend(float(m.group(1)) for m in re.finditer(
                r"\bRPM\s*[:=]?\s*(\d+(?:\.\d+)?)\b", answer, re.I))
        if target["unit"] == "%" and re.search(r"\befficiency\b|\beff(?:iciency)?[- ]?map\b", answer, re.I):
            values.extend(float(m.group(1)) * 100 for m in re.finditer(
                r"(?<![\w.])(0\.\d{3,6})\b", answer))
        if not values and target["unit"] in ("ratio", "dimensionless"):
            values = [float(x) for x in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", answer)]
        nearest = min(values, key=lambda x: abs(x-target["value"])) if values else None
        error = abs(nearest-target["value"]) if nearest is not None else None
        tol = target.get("tolerance_abs") or 0
        details.append({"expected": target, "returned": nearest, "absolute_error": error,
                        "passed": error is not None and error <= tol})
    return sum(d["passed"] for d in details)/len(details), details


def score(case, answer, hits):
    facts = case.get("required_facts", [])
    present = [bool(re.search(x, answer, re.I)) for x in facts]
    fact_score = sum(present)/len(present) if facts else None
    numerical, numbers = numerical_score(answer, case.get("expected_numerical_values", []))
    source = case.get("relevant_document_or_chunk", "")
    expected_doc = source.split("/")[-1].split(".")[0].lower() if case.get("expected_source") == "document" else None
    retrieved = bool(expected_doc and any(expected_doc in h["source"].lower() for h in hits))
    citations = re.findall(r"\[([^\]]+)\]", answer)
    cited = bool(expected_doc and any(expected_doc in c.lower() for c in citations))
    unavailable = case.get("expected_behavior_if_unavailable") is not None
    refused = bool(re.search(r"\b(?:not (?:provided|available|given|found|enough|configured)|no (?:data|result|evidence|rating|limit|product-specific|independent)|cannot (?:determine|verify|establish)|can't (?:determine|verify|establish|find)|couldn't find|insufficient|unknown|unavailable|clarify|which (?:result|parameter|input))\b", answer, re.I))
    # Forbidden strings are reported as flags only: negated uses need review.
    forbidden = [p for p in case.get("forbidden_claims", []) if re.search(p, answer, re.I)]
    parts = [v for v in (fact_score, numerical, float(cited) if expected_doc and retrieved else None,
                         float(refused) if unavailable else None) if v is not None]
    return {"rule_score": sum(parts)/len(parts) if parts else None,
            "required_fact_recall": fact_score, "fact_matches": present,
            "numerical_accuracy": numerical, "numerical_details": numbers,
            "expected_source_retrieved": retrieved if expected_doc else None,
            "expected_source_cited": cited if expected_doc else None,
            "unavailable_refusal": refused if unavailable else None,
            "forbidden_flags_for_review": forbidden,
            "word_count": len(answer.split()),
            "voice_length_ok": len(answer.split()) <= 55 if case.get("model_role") == "fast" else None}
