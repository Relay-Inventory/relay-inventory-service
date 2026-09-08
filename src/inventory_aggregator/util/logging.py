from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {"event": event, "timestamp": datetime.utcnow().isoformat()}
    payload.update(fields)
    logger.info(json.dumps(payload))
