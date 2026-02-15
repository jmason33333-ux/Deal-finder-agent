"""Centralized logging setup."""

import logging
import sys
from datetime import datetime
from src.config import LOG_DIR, LOG_LEVEL

LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(console)

    # File handler - one log per day
    log_file = LOG_DIR / f"run_{datetime.now():%Y-%m-%d}.log"
    fh = logging.FileHandler(log_file)
    fh.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(fh)

    return logger
