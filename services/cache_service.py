import logging
import json
from typing import Any, Optional
from datetime import timedelta
import redis

# Use the centralized redis connection from main.py if possible,
# or create a local client for services using the URL from Config.
from app_settings import Config

logger = logging.getLogger(__name__)

class CacheService:
    """
    Staff+ Distributed Caching Service (v15.0.0)
    Replaces in-memory TTLCache with Redis to support scaling and persist across restarts.
    Versioning (v15.1.0): Prefixes all keys with V1_PREFIX to handle schema migrations.
    """
    VERSION_PREFIX = "v1:"

    def __init__(self):
        try:
            self.redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
            self.redis.ping()
            logger.info(f"CacheService: Redis Connection Online [Prefix: {self.VERSION_PREFIX}]")
        except Exception as e:
            logger.error(f"CacheService: Redis Connection Failed: {e}")
            self.redis = None

    def _get_v_key(self, key: str) -> str:
        """Staff+ Version Guard: Ensures all keys are properly prefixed (v15.1.0)"""
        if key.startswith(self.VERSION_PREFIX):
            return key
        return f"{self.VERSION_PREFIX}{key}"

    def get(self, key: str) -> Optional[Any]:
        if not self.redis: return None
        v_key = self._get_v_key(key)
        try:
            data = self.redis.get(v_key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f"Cache Get Error [{v_key}]: {e}")
            return None

    def set(self, key: str, value: Any, ttl_seconds: int = 600):
        if not self.redis: return
        v_key = self._get_v_key(key)
        try:
            self.redis.set(v_key, json.dumps(value), ex=ttl_seconds)
        except Exception as e:
            logger.error(f"Cache Set Error [{v_key}]: {e}")

    def set_nx(self, key: str, value: Any, ttl_seconds: int = 600) -> bool:
        """Atomic Set-if-not-exists with TTL (v15.2.0). 
        Returns True if set, False if already exists."""
        if not self.redis: return False
        v_key = self._get_v_key(key)
        try:
            return bool(self.redis.set(v_key, json.dumps(value), ex=ttl_seconds, nx=True))
        except Exception as e:
            logger.error(f"Cache Set-NX Error [{v_key}]: {e}")
            return False

    def delete(self, key: str):
        if not self.redis: return
        v_key = self._get_v_key(key)
        try:
            self.redis.delete(v_key)
        except Exception as e:
            logger.error(f"Cache Delete Error [{v_key}]: {e}")

# Global instance
cache_service = CacheService()
