from utils.logging_config import setup_logging
setup_logging()

from fastapi import FastAPI, Request, HTTPException, Depends, Header, Query, BackgroundTasks, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBearer
from dotenv import load_dotenv
import os
import io
import logging
import uuid
import asyncio
import re
import hmac
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
import redis
import redis.asyncio as aioredis
from rq import Queue
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from middleware.request_id_middleware import RequestIDMiddleware
from middleware.latency_middleware import LatencyMiddleware

# -----------------------------
# 1. Critical Boot Sequence
# -----------------------------
load_dotenv()
from app_settings import Config
print(f"BOOT_LOG: Discovery Chain -> {Config.REDIS_URL[:20]}...")
# Aggressive Scan: Identify any URL-like variables for debugging
for k, v in os.environ.items():
    if v and (v.startswith("redis://") or v.startswith("http://") or v.startswith("https://")):
         print(f"BOOT_LOG: URL-Like Entry Found -> {k}: {v[:15]}...")
print(f"BOOT_LOG: Environment Keys -> {', '.join(k for k in os.environ.keys() if 'KEY' not in k.upper() and 'SECRET' not in k.upper())}")
Config.validate()

# -----------------------------
# 2. Services & Lifespan
# -----------------------------
from services.supabase_service import supabase_service
from services.analytics_service import analytics_service
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting EresumeHub API [Staff+ Hardened]...")
    os.makedirs(Config.RAG_SCHEMAS_DIR, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    if hasattr(supabase_service, "initialize"):
        supabase_service.initialize(Config.SUPABASE_URL, Config.SUPABASE_KEY)
            
    # Initialize Redis SAFELY (v16.4.5 Sync Architecture)
    try:
        raw_url = Config.REDIS_URL
        masked_url = re.sub(r':([^@]+)@', ':****@', raw_url) if '@' in raw_url else raw_url
        logger.info(f"System Check: Redis Endpoint Identified -> {masked_url}")
        
        # Async Handle (For routes, idempotency, debounce)
        logger.info("Initializing Async Redis Handle...")
        redis_async = aioredis.from_url(raw_url, decode_responses=False)
        
        app.state.redis = redis_async
        
        logger.info("Distributed Job System: Offline (Synchronous Native Mode)")
    except Exception as e:
        logger.critical(f"FATAL: Redis connection failed: {e}")
        app.state.redis = None
        
        # Staff+ Production Seal: Hard fail if infra is missing in production
        if Config.ENVIRONMENT == "production":
            raise RuntimeError(f"CRITICAL INFRASTRUCTURE FAILURE: Redis is REQUIRED in production environment. {e}")

    yield
    if hasattr(app.state, "redis") and app.state.redis:
        await app.state.redis.close()
    logger.info("Shutting down EresumeHub API...")

# -----------------------------
# 3. Initialize FastAPI
# -----------------------------
app = FastAPI(
    title="EresumeHub API",
    description="Enterprise-grade ATS-friendly resume generation (Hardened v3.14.0)",
    version="16.4.15",
    lifespan=lifespan
)

# --- CRITICAL: FAST PORT DETECTION (v16.4.18) ---
@app.get("/")
async def root():
    """Essential Health Check for Render Port Binding Detection."""
    return {"status": "online", "message": "EresumeHub API [Hardened]", "version": "16.4.15"}

# 4. Middleware & Security (CORS MUST BE FIRST)
# -----------------------------
# Staff+ Hardening: CORS preflight must be caught before any other middleware
# Hardcoding production origins to bypass potential .env synchronization lag on Render
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://e-resumehub.netlify.app",
        "https://eresumehub.com",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# 5. Performance Observability (v16.4.4 Convergence)
# -----------------------------

# Register in LIFO order for request-handling (FIFO for response-handling)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LatencyMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)

print(f"BOOT_LOG: CORS Trusted Origins -> {Config.ALLOWED_ORIGINS}")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

async def verify_api_key(api_key: str = Header(None, alias="X-API-Key")):
    """Constant-time API Key verification."""
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key missing")
    if not hmac.compare_digest(api_key, Config.API_SECRET_KEY):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

# -----------------------------
# 5. Global Exception Shielding
# -----------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    status_code = getattr(exc, "status_hint", 500)
    error_code = getattr(exc, "code", "INTERNAL_ERROR")
    
    # 🕵️ Global Audit Trace (v16.4.1)
    logger.exception(f"Unhandled Exception [{request_id}] ({type(exc).__name__}): {exc}")
    
    # 🛡️ Total Transparency Mode (v16.4.2 Hardening)
    # Temporarily force-exposing all exceptions even in production to catch the hidden crash.
    user_message = f"Internal Exception: {str(exc)} [{type(exc).__name__}]"
    if isinstance(exc, PipelineError):
        # Only expose known user-safe messages, mask internal paths
        user_message = getattr(exc, "message", "A pipeline error occurred.")
        if "STORAGE_FAIL" in getattr(exc, "code", ""):
            user_message = "Storage service is currently unavailable."
            
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False, 
            "error": user_message,
            "code": error_code,
            "request_id": request_id
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.detail, "request_id": getattr(request.state, "request_id", "unknown")}
    )

# -----------------------------
# 6. Global Health & Identity (v16.4.6 Canonical)
# -----------------------------

@app.get("/api/health")
async def health_check():
    """Detailed System Health Check."""
    return {"status": "online", "version": "16.4.15"}

from routes.auth import router as auth_router
from routes.resume_routes import router as resume_router
from routes.job_routes import router as job_router
from routes.profile_router import router as profile_router
from routes.schema_router import router as schema_router
from routes.analytics_router import router as analytics_router
from routes.system_routes import router as system_router
from routes.user_routes import router as user_router
from utils.auth_deps import get_current_user_id

app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(resume_router)

# -----------------------------
# 8. Global Health & Identity (v16.4.6 Canonical)
# -----------------------------
from utils.auth_deps import get_current_user_from_token

@app.get("/api/v1/user/me", tags=["Auth"])
async def get_current_user_identity_proxy(request: Request):
    """
    Canonical Identity Proxy (v16.4.6). 
    Resolves identity using the hardened service layer to prevent manual route invocation regressions.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return JSONResponse(status_code=401, content={"success": False, "error": "Missing Authorization header"})
        
    try:
        user = await get_current_user_from_token(request, auth_header)
        return {
            "success": True,
            "data": user
        }
    except Exception as e:
        logger.error(f"Identity Proxy Failure: {e}")
        return JSONResponse(
            status_code=401, 
            content={"success": False, "error": str(e)}
        )

app.include_router(job_router)
app.include_router(profile_router)
app.include_router(schema_router)
app.include_router(analytics_router)
app.include_router(system_router)
app.include_router(user_router)

# -----------------------------
# 7. Optimized Performance Endpoints
# -----------------------------
@app.get("/api/v1/resume/{resume_id}/pdf", tags=["Resumes"])
async def download_resume_pdf_proxied(
    request: Request,
    resume_id: str, 
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id)
):
    """Staff+ Zero-Memory Secure PDF Delivery via Signed URL Redirect."""
    try:
        # 1. Ownership validation
        resume = await supabase_service.get_resume(resume_id)
        if not resume or resume.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized access to this resume")

        # 2. Log in background to prevent event-loop-blocking or request-teardown dropping
        background_tasks.add_task(
            supabase_service.log_resume_download, 
            resume_id, 
            {
                "visitor_ip": request.client.host if request.client else "127.0.0.1",
                "request_id": getattr(request.state, "request_id", "unknown")
            }
        )
        
        # 3. Create signed URL for zero-memory direct download
        # This bypasses the API server RAM entirely!
        signed_url = await supabase_service.get_resume_signed_url(user_id, resume_id)
        
        return RedirectResponse(url=signed_url)
        
    except Exception as e:
        logger.error(f"Secure PDF Redirect failed: {e}")
        raise HTTPException(status_code=404, detail="PDF file not found")