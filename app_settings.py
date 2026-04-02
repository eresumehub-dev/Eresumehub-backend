import os
import logging
from typing import List
from dotenv import load_dotenv

# Initialize Environment
load_dotenv()

logger = logging.getLogger(__name__)

class Config:
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "").strip('"').strip("'")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "").strip('"').strip("'")
    
    API_SECRET_KEY: str = os.getenv("API_SECRET_KEY", "dev-secret-key-change-in-production")
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", 10))
    MAX_FILE_SIZE_BYTES: int = MAX_FILE_SIZE_MB * 1024 * 1024
    ALLOWED_ORIGINS: List[str] = [origin.strip().rstrip("/") for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",") if origin.strip()]
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL", 3600))
    AI_REQUEST_TIMEOUT: int = int(os.getenv("AI_REQUEST_TIMEOUT", 120))
    DEFAULT_MODEL: str = "mistralai/mistral-7b-instruct:free"
    FALLBACK_MODEL: str = os.getenv("FALLBACK_MODEL", "gpt-4")
    AI_PROVIDER_ORDER: str = os.getenv("AI_PROVIDER_ORDER", "groq,gemini,openrouter")
    AI_TEST_MODE: bool = os.getenv("AI_TEST_MODE", "False").lower() == "true"
    AI_TEST_PROVIDER: str = os.getenv("AI_TEST_PROVIDER", "openrouter:mistral-7b")
    RAG_SCHEMAS_DIR: str = "rag_schemas"
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", "uploads")
    ENABLE_LOGGING: bool = os.getenv("ENABLE_LOGGING", "True").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", 8000))
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    # Deep Discovery Chain (v3.22.0)
    @classmethod
    def _discover_redis_url(cls) -> str:
        # 1. Primary explicit keys
        for key in ["ERESUME_REDIS_URL", "INTERNAL_REDIS_URL", "REDIS_URL", "REDIS_INTERNAL_URL"]:
            val = os.getenv(key)
            if val and "://" in val:
                return val
        
        # 2. Universal Probe: Scan all keys for any Redis-like URL
        for key, val in os.environ.items():
            if "REDIS" in key.upper() and val and "://" in val:
                return val
                
        # 3. Fallback to localhost (Standard dev)
        return "redis://localhost:6379"

    REDIS_URL: str = _discover_redis_url.__func__(None) if hasattr(_discover_redis_url, "__func__") else _discover_redis_url()

    @classmethod
    def validate(cls):
        if not cls.OPENROUTER_API_KEY:
            logger.warning("OPENROUTER_API_KEY not configured - AI features will be limited")
        if not cls.SUPABASE_URL or not cls.SUPABASE_KEY:
            logger.warning("SUPABASE not configured - Supabase features will not work")
        if cls.AI_TEST_MODE:
            logger.info(f"AI_TEST_MODE is ENABLED. Forcing provider: {cls.AI_TEST_PROVIDER}")
