from collections import deque
from datetime import datetime
import logging
from logging import config

logging_config = {
    "version": 1,
    "formatters": {
        "default": {"format": "[%(asctime)s: %(levelname)s/%(name)s]: %(message)s"}
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "default",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "default",
            "filename": "./logs/harness.log",
            "mode": "a",
            "encoding": "utf-8",
            "maxBytes": 1000000,
            "backupCount": 9,
        },
    },
    "root": {"level": "DEBUG", "handlers": ["console", "file"]},
    "disable_existing_loggers": False,
}


class ReasonerLogEntryFormatter(logging.Formatter):
    """Format to match Reasoner API LogEntry"""

    def format(self, record):
        log_entry = {}

        # If given a string use that as the message
        if isinstance(record.msg, str):
            log_entry["message"] = record.msg

        # If given a dict, just use that as the log entry
        # Make sure everything is serializeable
        if isinstance(record.msg, dict):
            log_entry |= record.msg

        # Add timestamp
        iso_timestamp = datetime.utcfromtimestamp(record.created).isoformat()
        log_entry["timestamp"] = iso_timestamp

        # Add level
        log_entry["level"] = record.levelname

        return log_entry


class QueryLogHandler(logging.Handler):
    """Log Handler."""

    def __init__(self, log_queue):
        logging.Handler.__init__(self)
        self.log_queue = log_queue

    def emit(self, record):
        # put newer messages in front
        self.log_queue.appendleft(self.format(record))

    def contents(self):
        """Get stored logs from handler."""
        return self.log_queue


# Create unique logger for each query
# https://stackoverflow.com/a/37967421
class QueryLogger(object):
    """Query-specific logger."""

    def __init__(self, maxlen=None):
        self._log_queue = deque(maxlen=maxlen)
        self._log_handler = QueryLogHandler(self._log_queue)
        self._log_handler.setFormatter(ReasonerLogEntryFormatter())

    @property
    def log_handler(self):
        return self._log_handler


def get_logger(qid, log_level):
    log_handler = QueryLogger().log_handler
    logger = logging.getLogger(f"harness.{qid}")
    level_number = logging._nameToLevel[log_level]
    logger.setLevel(level_number)
    logger.addHandler(log_handler)
    return logger


def setup_logger():
    config.dictConfig(logging_config)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("root").setLevel(logging.WARNING)
    logging.getLogger("bmt").setLevel(logging.ERROR)
