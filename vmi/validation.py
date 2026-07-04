"""Lightweight input validation for CustomTkinter entry widgets.

Highlights offending fields with a red border and returns parsed values, so
the app can refuse to plot on bad input instead of throwing deep in a
calculation. Nothing here changes any formula.
"""

from .theme import COLORS


class ValidationError(Exception):
    """Raised when one or more input fields are invalid."""
    def __init__(self, messages):
        self.messages = messages
        super().__init__("; ".join(messages))


def _mark(entry, ok):
    """Colour an entry's border green-ish (ok) or red (bad)."""
    if entry is None:
        return
    try:
        entry.configure(border_color=COLORS["border"] if ok else COLORS["danger"])
    except Exception:
        pass


def parse_float(entry, name, *, allow_blank=False, default=None,
                minimum=None, maximum=None, errors=None):
    """Parse a CTkEntry's text as float with bounds checking.

    On failure, appends a message to `errors` (if given), red-borders the
    field, and returns `default`. On success, clears the border and returns
    the float.
    """
    raw = ""
    try:
        raw = entry.get().strip()
    except Exception:
        pass

    if raw == "":
        if allow_blank:
            _mark(entry, True)
            return default
        msg = f"{name} is required."
        if errors is not None:
            errors.append(msg)
        _mark(entry, False)
        return default

    try:
        val = float(raw)
    except ValueError:
        msg = f"{name} must be a number (got '{raw}')."
        if errors is not None:
            errors.append(msg)
        _mark(entry, False)
        return default

    if minimum is not None and val < minimum:
        msg = f"{name} must be >= {minimum}."
        if errors is not None:
            errors.append(msg)
        _mark(entry, False)
        return default
    if maximum is not None and val > maximum:
        msg = f"{name} must be <= {maximum}."
        if errors is not None:
            errors.append(msg)
        _mark(entry, False)
        return default

    _mark(entry, True)
    return val


def clear_marks(entries):
    for e in entries:
        _mark(e, True)
