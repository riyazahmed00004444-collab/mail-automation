"""
sender/batch_processor.py — Legacy shim.
The actual sending logic has moved to sender/engine.py.
This file is kept to avoid breaking any imports that reference the old module.
"""
from .engine import run_once
import logging

logger = logging.getLogger(__name__)


def process_queue(batch_size: int = None) -> dict:
    """
    Deprecated: use sender.engine.run_once() directly.
    Kept for backward compatibility with any existing callers.
    """
    logger.info('batch_processor.process_queue() called — delegating to engine.run_once()')
    return run_once()
