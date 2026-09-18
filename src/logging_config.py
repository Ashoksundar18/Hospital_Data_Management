import logging

_logging_configured = False


def configure_logging():
    global _logging_configured
    if not _logging_configured:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        _logging_configured = True
