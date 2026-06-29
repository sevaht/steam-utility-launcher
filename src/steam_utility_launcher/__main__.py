import logging
import sys

from .application import main as application_main

# Given application.configure_logging(), will only log to file because
# uncaught exceptions provide perfectly sufficient console output.
logger = logging.getLogger(__name__)


def main() -> None:
    try:
        sys.exit(application_main())
    except Exception:
        logger.exception("Unhandled exception")
        raise


if __name__ == "__main__":
    main()
