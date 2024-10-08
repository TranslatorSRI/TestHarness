"""Logging setup."""

import logging


class ColoredFormatter(logging.Formatter):
    """Colored formatter."""

    prefix = "[%(asctime)s: %(levelname)s/%(name)s]:"
    default = f"{prefix} %(message)s"
    error_fmt = f"\x1b[31m{prefix}\x1b[0m %(message)s"
    warning_fmt = f"\x1b[33m{prefix}\x1b[0m %(message)s"
    info_fmt = f"\x1b[32m{prefix}\x1b[0m %(message)s"
    debug_fmt = f"\x1b[34m{prefix}\x1b[0m %(message)s"

    def __init__(self, fmt=default):
        """Initialize."""
        logging.Formatter.__init__(self, fmt)

    def format(self, record):
        """Format record."""
        format_orig = self._style._fmt
        if record.levelno == logging.DEBUG:
            self._style._fmt = ColoredFormatter.debug_fmt
        elif record.levelno == logging.INFO:
            self._style._fmt = ColoredFormatter.info_fmt
        elif record.levelno == logging.WARNING:
            self._style._fmt = ColoredFormatter.warning_fmt
        elif record.levelno == logging.ERROR:
            self._style._fmt = ColoredFormatter.error_fmt
        result = logging.Formatter.format(self, record)
        self._style._fmt = format_orig
        return result


def setup_logger():
    """Set up Test Harness logger."""
    logger = logging.getLogger("harness")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(ColoredFormatter())
    logger.addHandler(handler)

    return logger


def assert_no_level(logger, allowed_level, exceptions=0):
    """
    Check that the logger has no records greater than
    the allowed level.

    Also has a parameter to specify the number of exceptions
    to the rule (number of records that will be ignored).
    """
    total = 0
    for record in logger.records:
        if record.levelno >= allowed_level:
            total += 1
        if total > exceptions:
            raise Exception(f"Invalid Log Record: {record}")
