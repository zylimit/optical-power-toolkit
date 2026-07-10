"""Console + rotating file logging (UTF-8 safe on Windows)."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import config

_LOGGER_NAME = "onebox_downloader"


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(getattr(logging, level, logging.INFO))
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(ch)

    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            os.path.join(config.LOG_DIR, "onebox_downloader.log"),
            maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"))
        logger.addHandler(fh)
    except OSError:
        pass  # console-only is fine if the log dir can't be created
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)
