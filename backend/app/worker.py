"""Worker stub — job processing será implementado no Bloco 4."""
import logging
import time

from app.core import logging as app_logging

app_logging.setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("worker_started", extra={"status": "stub"})
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
