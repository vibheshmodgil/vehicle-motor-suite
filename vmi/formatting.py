"""Small formatting helpers so the UI shows sensible precision instead of
14-digit floats like 42.97891402606307."""


def fmt(value, unit="", decimals=2):
    """Format a number with fixed decimals and an optional unit suffix."""
    try:
        s = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)
    return f"{s} {unit}".strip()


def fmt_wh(value):
    return fmt(value, "Wh", 1)


def fmt_km(value):
    return fmt(value, "km", 2)


def fmt_pct(value):
    """value is a fraction in [0,1] -> percentage with 2 dp."""
    try:
        return f"{float(value) * 100.0:.2f}%"
    except (TypeError, ValueError):
        return str(value)
