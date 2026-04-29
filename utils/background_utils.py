import logging
import asyncio
from typing import Callable, Any
from functools import wraps

logger = logging.getLogger(__name__)

async def safe_background_task(func: Callable, *args, **kwargs):
    """
    Staff+ Resilience Wrapper: Ensures background tasks never bubble exceptions
    to Starlette/FastAPI, preventing 502 Bad Gateway and worker crashes.
    """
    try:
        if asyncio.iscoroutinefunction(func):
            await func(*args, **kwargs)
        else:
            func(*args, **kwargs)
    except Exception as e:
        logger.error(f"Background task {func.__name__ if hasattr(func, '__name__') else str(func)} failed: {e}")
        # Log full stack trace for engineering audit
        logger.exception(e)
