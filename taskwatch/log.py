import logging
import sys

from .paths import DATA_DIR


def setup_logger(name: str = "taskwatch", level: int = logging.WARNING) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(DATA_DIR / "taskwatch.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)

        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(level)
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(sh)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"taskwatch.{name}" if name else "taskwatch")
