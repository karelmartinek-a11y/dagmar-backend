import logging
import os
from logging.handlers import RotatingFileHandler


DEFAULT_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "pid=%(process)d "
    "%(message)s"
)


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if not parent:
        return
    os.makedirs(parent, exist_ok=True)


def configure_logging(
    *,
    level: str = "INFO",
    log_file: str | None = None,
    access_log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Configure application logging.

    Production intent:
    - When running under systemd, journald will capture stdout/stderr.
    - Optionally also write to log files under /var/log/dagmar/.

    This function is deterministic and safe to call multiple times.
    """

    # Normalize level
    level_value = getattr(logging, level.upper(), logging.INFO)

    # Reset root handlers to avoid duplicated logs on reload.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(level_value)

    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)

    # Always log to stdout (systemd/journald friendly)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level_value)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Optional file logs
    if log_file:
        _ensure_parent_dir(log_file)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        file_handler.setLevel(level_value)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if access_log_file:
        _ensure_parent_dir(access_log_file)
        access_handler = RotatingFileHandler(
            access_log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        access_handler.setLevel(level_value)
        access_handler.setFormatter(formatter)
        logging.getLogger("dagmar.access").addHandler(access_handler)

    # Tame noisy loggers
    logging.getLogger("uvicorn").setLevel(level_value)
    logging.getLogger("uvicorn.error").setLevel(level_value)
    logging.getLogger("uvicorn.access").setLevel(level_value)
    logging.getLogger("gunicorn").setLevel(level_value)
    logging.getLogger("gunicorn.error").setLevel(level_value)
    logging.getLogger("gunicorn.access").setLevel(level_value)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
