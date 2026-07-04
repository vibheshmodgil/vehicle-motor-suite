"""App-wide error logging.

Many GUI paths use `try/except Exception: pass` to keep the window alive on
bad input; that's fine for the user but leaves nothing to diagnose with.
This module gives the app one rotating log file (`vmi_app.log`, next to
main.py) that uncaught Tk-callback errors are written to -- see the
`report_callback_exception` hook installed in app.py.
"""

import logging
import logging.handlers

LOG_PATH = "vmi_app.log"

logger = logging.getLogger("vmi")


def setup():
    """Configure (once) and return the app logger."""
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=500_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except Exception:
        # If the log file can't be opened (e.g. read-only dir), stay silent --
        # logging must never take the app down.
        logger.addHandler(logging.NullHandler())
    return logger
