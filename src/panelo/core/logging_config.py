"""Safe rotating-file logging configuration."""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "panelo"
_OWNED_HANDLER = "_panelo_owned_handler"
_LOG_PATH = "_panelo_log_path"


class UtcFormatter(logging.Formatter):
    @staticmethod
    def converter(timestamp: float | None = None) -> time.struct_time:
        return time.gmtime(timestamp)


def _owned_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [handler for handler in logger.handlers if getattr(handler, _OWNED_HANDLER, False)]


def shutdown_logging() -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    for handler in _owned_handlers(logger):
        logger.removeHandler(handler)
        handler.close()


def configure_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    formatter = UtcFormatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    target_path = log_dir / "panelo.log"
    handlers = _owned_handlers(logger)
    if len(handlers) == 1 and getattr(handlers[0], _LOG_PATH, None) == target_path:
        handlers[0].setFormatter(formatter)
        return logger
    shutdown_logging()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            target_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    setattr(handler, _OWNED_HANDLER, True)
    setattr(handler, _LOG_PATH, target_path)
    logger.addHandler(handler)
    return logger
