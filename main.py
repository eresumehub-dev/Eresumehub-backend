from fastapi import FastAPI, Request, HTTPException, Depends, Header, UploadFile, File, Form, BackgroundTasks, Query, status
# Force Reload Timestamp: 2026-02-16 16:35 Fixed Imports
from fastapi.responses import JSONResponse, StreamingResponse
# Reload trigger 2026-01-07 Reloaded for Gemini v1 stability
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, validator, EmailStr
from dotenv import load_dotenv
import httpx
import os
import io
import json
import logging
import uuid
import asyncio
import re
import hashlib
from typing import List, Optional, Dict, Any, Union
from uuid import uuid4
from configurations.countries import get_country_context, COUNTRY_RULES
from datetime import datetime
from contextlib import asynccontextmanager
from functools import lru_cache


from tenacity import retry, stop_after_attempt, wait_exponential
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from jinja2 import Environment, FileSystemLoader

# Import xhtml2pdf for PDF generation
from xhtml2pdf import pisa

# Services
from services.supabase_service import supabase_service
from services.profile_service import ProfileService
from services.ai_service import ai_service
from services.rag_service import RAGService
from services.rag_service import RAGService
from services.analytics_service import AnalyticsService
from routes.auth import router as auth_router
from routes.schema_router import router as schema_router
from routes.analytics_router import router as analytics_router
from routes.profile_router import router as profile_router
from utils.supabase_client import supabase
from utils.file_processor import FileProcessor
from utils.resume_validator import ResumeComplianceValidator
from services.resume_autocorrect import resume_autocorrect
from services.rag_rule_loader import rag_rule_loader

# Initialize Profile Service
profile_service = ProfileService(supabase_service)
analytics_service = AnalyticsService(supabase_service)

def html_to_pdf(html_content: str) -> bytes:
    """Convert HTML string to PDF bytes using xhtml2pdf"""
    # Character Sanitization for PDF rendering (xhtml2pdf lacks full Unicode support for default fonts)
    # character sanitization for PDF (xhtml2pdf is picky)
    def clean_text(t):
        if not isinstance(t, str): return t
        # Standard replacements
        replacements = {
            '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-', '\u2014': '--',
            '\u2015': '--', '\u2017': '_', '\u2018': "'", '\u2019': "'", '\u201a': "'",
            '\u201c': '"', '\u201d': '"', '\u201e': '"', '\u2022': '*', '\u2026': '...',
            '\u00a0': ' ', '\xad': '-'
        }
        for char, rep in replacements.items():
            t = t.replace(char, rep)
        # Remove invisible characters that break xhtml2pdf kerning
        t = "".join(char for char in t if ord(char) >= 32 or char in "\n\r\t")
        return " ".join(t.split()) # Normalize whitespace gaps

    # We apply this cleaning to the raw HTML content for safety
    html_content = clean_text(html_content)

    pdf_buffer = io.BytesIO()
    try:
        pisa_status = pisa.CreatePDF(
            io.BytesIO(html_content.encode('utf-8')),
            dest=pdf_buffer,
            encoding='utf-8'
        )
        if pisa_status.err:
            raise Exception("Failed to generate PDF (pisa_status.err)")
    except Exception as e:
        import traceback
        with open("crash_report_server.txt", "w") as f:
            f.write(f"ERROR: {str(e)}\n\n{traceback.format_exc()}")
        raise e
        
    return pdf_buffer.getvalue()

bearer_scheme = HTTPBearer(auto_error=False)

# -----------------------------
# Configuration & Setup (Reloaded)
# -----------------------------
load_dotenv()

# Create necessary directories
os.makedirs("logs", exist_ok=True)
os.makedirs("rag_schemas", exist_ok=True)
os.makedirs("rag_schemas/germany", exist_ok=True)
os.makedirs("rag_schemas/india", exist_ok=True)

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/eresume_hub.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from app_settings import Config
Config.validate()

# -----------------------------
# Cache & Rate Limiter
# -----------------------------
from cachetools import TTLCache
cache = TTLCache(maxsize=100, ttl=Config.CACHE_TTL_SECONDS)
limiter = Limiter(key_func=get_remote_address)

# Production Hardening Caches
ai_request_debounce_cache = TTLCache(maxsize=500, ttl=30)  # 30s debounce
ai_service_cooldown_cache = TTLCache(maxsize=1000, ttl=30)
  # 90s cooldown

# -----------------------------
# Lifespan Management
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting EresumeHub API...")
    Config.validate()
    os.makedirs(Config.RAG_SCHEMAS_DIR, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    if hasattr(supabase_service, "initialize"):
        try:
            supabase_service.initialize(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        except Exception as e:
            logger.warning(f"Supabase service initialization warning: {e}")
    logger.info("EresumeHub API startup complete")
    yield
    logger.info("Shutting down EresumeHub API...")

# -----------------------------
# Initialize FastAPI
# -----------------------------
app = FastAPI(
    title="EresumeHub API",
    description="Enterprise-grade ATS-friendly resume generation with AI + Supabase",
    version="3.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

from fastapi.openapi.utils import get_openapi

def add_auth_security_to_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})
    openapi_schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT"
    }
    openapi_schema["components"]["securitySchemes"]["APIKeyHeader"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key"
    }

    openapi_schema["security"] = [
        {"BearerAuth": []}
    ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema
app.openapi = add_auth_security_to_openapi

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# CORS & GZip are added later to ensure they wrap other middlewares correctly

# Request ID Middleware
@app.get("/api/health")
async def health_check():
    from utils.supabase_client import verify_connection
    connected, message = await verify_connection()
    return {
        "status": "online",
        "database": "connected" if connected else "disconnected",
        "db_message": message
    }

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# include the auth router
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
# include the schema router
app.include_router(schema_router)
app.include_router(analytics_router)
app.include_router(profile_router)

# -----------------------------
# Outermost Middleware (Last Added = First Executed)
# -----------------------------
# CORS Middleware must be outermost to handle OPTIONS correctly
has_wildcard = "*" in Config.ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if has_wildcard else Config.ALLOWED_ORIGINS,
    allow_origin_regex=".*" if has_wildcard else None,
    # Standard CORS: allow_credentials must be False if using wildcard "*"
    # But allow_origin_regex can bypass this while allowing all origins.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# -----------------------------
# Authentication Dependencies
# -----------------------------
# -----------------------------
# Authentication Dependencies
# -----------------------------
from utils.auth_deps import (
    get_current_user_from_token,
    get_current_user_id,
    get_current_user_ids,
    get_optional_user_id
)

# Alias for backward compatibility if needed
get_current_user = get_current_user_from_token


@app.get("/api/v1/profile")
async def get_user_profile(user = Depends(get_current_user_ids)):
    """Get user's complete profile including work experiences and education"""
    try:
        # Use a safety timeout to prevent hanging the infinite event loop
        # The frontend has a 10s timeout, we use slightly less (8s)
        try:
            # Try fetching by auth_user_id first
            profile = await asyncio.wait_for(
                profile_service.get_profile(user["auth_user_id"]),
                timeout=15.0
            )
            
            # If not found, try by platform_user_id
            if not profile:
                logger.info(f"Profile not found by auth_id, trying platform_id {user['platform_user_id']}")
                profile = await asyncio.wait_for(
                    profile_service.get_profile(user["platform_user_id"]),
                    timeout=15.0
                )
        except asyncio.TimeoutError:
            logger.error(f"Profile fetch timed out for user {user.get('auth_user_id')}")
            return {"profile": None, "exists": False, "error": "Database timeout"}
            
        if not profile:
            return {"profile": None, "exists": False}
        return {"profile": profile, "exists": True}
    except HTTPException:
        # Re-raise HTTP exceptions (like 401 from auth)
        raise
    except Exception as e:
        logger.error(f"Error fetching profile for user {user.get('auth_user_id')}: {str(e)}")
        # Return empty profile instead of 500 error for missing profiles
        return {"profile": None, "exists": False, "error": str(e)}

@app.post("/api/v1/profile", tags=["Profile"])
async def create_or_update_profile(
    profile_data: dict,
    user = Depends(get_current_user_ids)
):
    """Create or update user profile"""
    try:
        # Use auth_user_id since user_profiles.user_id references auth.users(id)
        profile = await asyncio.wait_for(
            profile_service.create_or_update_profile(user["auth_user_id"], profile_data),
            timeout=15.0 # Give more time for creation/update
        )
        return {"success": True, "profile": profile}
    except asyncio.TimeoutError:
        logger.error(f"Profile update timed out for user {user.get('auth_user_id')}")
        raise HTTPException(status_code=504, detail="Database timeout during profile update")
    except Exception as e:
        logger.error(f"Error creating/updating profile: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/profile/generate-summary", tags=["Profile"])
async def generate_profile_summary(
    profile_data: dict,
    user = Depends(get_current_user_ids)
):
    """Generate a professional summary using AI based on profile data"""
    try:
        summary = await asyncio.wait_for(
            ai_service.generate_simple_summary(profile_data),
            timeout=20.0
        )
        return {"success": True, "summary": summary}
    except asyncio.TimeoutError:
        logger.error(f"Summary generation timed out for user {user.get('auth_user_id')}")
        raise HTTPException(status_code=504, detail="AI generation timed out")
    except Exception as e:
        logger.error(f"Error generating summary: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/profile/upload-photo", tags=["Profile"])
async def upload_profile_photo(
    file: UploadFile = File(...),
    user = Depends(get_current_user_ids)
):
    """Upload profile photo and update user profile"""
    try:
        user_id = user["auth_user_id"]
        # Validate file type
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="File must be an image")
            
        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large (max 5MB)")

        # Unique filename
        ext = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
        filename = f"photo_{int(datetime.utcnow().timestamp())}.{ext}"
        
        # Upload using Supabase Service
        photo_url = await asyncio.wait_for(
            supabase_service.upload_profile_picture(user_id, content, filename),
            timeout=20.0
        )
        
        # Update profile with new photo_url (partial update now safe)
        await asyncio.wait_for(
            profile_service.create_or_update_profile(user_id, {"photo_url": photo_url}),
            timeout=10.0
        )
        
        return {"success": True, "photo_url": photo_url}
    except asyncio.TimeoutError:
        logger.error(f"Profile photo upload timed out for user {user.get('auth_user_id')}")
        raise HTTPException(status_code=504, detail="Upload timed out")
    except Exception as e:
        logger.error(f"Error uploading profile photo: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/profile/completion", tags=["Profile"])
async def get_profile_completion(user = Depends(get_current_user_ids)):
    """Get profile completion percentage"""
    try:
        percentage = await asyncio.wait_for(
            profile_service.get_profile_completion_percentage(user["auth_user_id"]),
            timeout=5.0
        )
        return {"completion_percentage": percentage}
    except Exception as e:
        logger.error(f"Error calculating profile completion: {str(e)}")
        return {"completion_percentage": 0, "error": str(e)}




@app.post("/api/v1/resume/improve", tags=["Resume Generation"])
async def improve_existing_resume(
    file: UploadFile = File(...),
    country: str = Form("Germany"),
    job_description: str = Form(""),
    user = Depends(get_current_user_ids)
):
    """Upload resume, get AI recommendations, and return improved version"""
    try:
        # Parse resume
        safe_filename = FileProcessor.validate_file(file)
        ext = os.path.splitext(safe_filename)[1].lower()
        
        if ext == '.pdf':
            result = FileProcessor.parse_pdf(file)
        elif ext == '.docx':
            result = FileProcessor.parse_docx(file)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
            
        text = result["text"]
        metadata = result["metadata"]
        
        # Construct explicit metadata string for AI
        meta_context = f"""
        [METADATA START]
        Detected Page Count: {metadata.get('page_count', 'Unknown')}
        Contains Photos/Images: {metadata.get('has_images', False)} (If True, DO NOT complain about missing photo)
        File Type: {metadata.get('file_type', 'Unknown')}
        [METADATA END]
        """
        
        # Get country-specific cultural context from RAG configurations
        country_context = get_country_context(country)
        
        # Use AI to improve resume with timeout
        improvement_prompt = f"""
        Improve this resume for a {country} job application.
        
        Country-Specific Rules:
        {country_context}
        
        Original Resume Context:
        {meta_context}
        
        Original Resume Text:
        {text[:5000]}
        
        Job Description:
        {job_description}
        
        Instructions:
        1. Fix all ATS compatibility issues.
        2. Apply {country}-specific formatting and structure (strictly follow the Country-Specific Rules above).
        3. Optimize keywords specifically for the job description.
        4. ABSOLUTE CONSTRAINT: DO NOT use Japanese formatting, 'Self-PR' sections, or Japanese characters unless the country is specifically JAPAN.
        5. Return ONLY the improved resume text in a clear, formatted professional layout.
        """
        
        improved_text = await asyncio.wait_for(
            ai_service._call_api(improvement_prompt, temperature=0.3),
            timeout=60.0
        )
        
        return {
            "success": True,
            "original_text": text[:500] + "...",
            "improved_text": improved_text,
            "country": country
        }
    except asyncio.TimeoutError:
        logger.error(f"Resume improvement timed out for user {user.get('auth_user_id')}")
        raise HTTPException(status_code=504, detail="AI Analysis timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error improving resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------
# Resume Management Endpoints
# -----------------------------

@app.get("/api/v1/user/me", tags=["Auth"])
async def get_my_details(user: Dict[str, Any] = Depends(get_current_user)):
    """Get current user details (including platform ID and username)"""
    return {"success": True, "data": user}

@app.get("/api/v1/resumes", tags=["Resumes"])
async def get_user_resumes(user: Dict[str, Any] = Depends(get_current_user_ids)):
    """Get all resumes for the current user using both Platform and Auth IDs"""
    try:
        user_ids = [user["platform_user_id"], user["auth_user_id"]]
        logger.info(f"DEBUG_AUTH: Fetching resumes for user_ids: {user_ids}")
        resumes = await supabase_service.get_user_resumes(user_ids)
        logger.info(f"DEBUG_AUTH: Found {len(resumes)} resumes for user_ids: {user_ids}")
        return {"success": True, "data": {"resumes": resumes}}
    except Exception as e:
        logger.error(f"Error fetching resumes: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/resumes/{resume_id}", tags=["Resumes"])
async def get_resume(resume_id: str, user: Dict[str, Any] = Depends(get_current_user_ids)):
    """Get a specific resume, authorizing by both Platform and Auth IDs"""
    try:
        resume = await supabase_service.get_resume(resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found")
        
        # Check if user owns this resume (either ID matches)
        if resume.get("user_id") not in [user["platform_user_id"], user["auth_user_id"]]:
            raise HTTPException(status_code=403, detail="Not authorized")
        return {"success": True, "data": resume}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/public/resumes/{username}/{slug}", tags=["Public"])
async def get_public_resume(username: str, slug: str, request: Request):
    """Get a public resume by username and slug"""
    try:
        resume = await supabase_service.get_resume_by_slug(username, slug)
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found or is private")
        
        return {"success": True, "data": resume}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching public resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/resumes/{resume_id}", tags=["Resumes"])
async def delete_resume(resume_id: str, user: Dict[str, Any] = Depends(get_current_user_ids)):
    """Delete a resume, authorizing by both Platform and Auth IDs"""
    try:
        # First verify it exists and user owns it
        resume = await asyncio.wait_for(
            supabase_service.get_resume(resume_id),
            timeout=5.0
        )
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found")
        
        # Check if user owns this resume (either ID matches)
        if resume.get("user_id") not in [user["platform_user_id"], user["auth_user_id"]]:
            raise HTTPException(status_code=403, detail="Not authorized")
            
        success = await asyncio.wait_for(
            supabase_service.delete_resume(resume_id),
            timeout=5.0
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete resume")
        return {"success": True}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Database timeout")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/resumes/{resume_id}/upload_pdf", tags=["Resumes"])
async def upload_original_pdf(
    resume_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id)
):
    """Upload an original PDF to replace the generated one (for ATS import fidelity)"""
    try:
        # 1. Fetch Resume to get slug
        resume = await supabase_service.get_resume(resume_id)
        if not resume or resume.get("user_id") != user_id:
             raise HTTPException(status_code=403, detail="Not authorized")
        
        # 2. Read file
        file_bytes = await file.read()
        
        # 3. Construct Path (Distinct from generated PDF)
        slug_filename = f"{resume.get('slug')}_original.pdf"
        
        # 4. Upload to Storage via Supabase Service
        original_pdf_url = await supabase_service.upload_resume_pdf(user_id, resume_id, file_bytes, slug_filename)
        
        # 5. Update Resume Meta 
        # We set BOTH original_pdf_url (perm) and pdf_url (current view)
        current_data = resume.get("resume_data", {})
        current_data["original_pdf_url"] = original_pdf_url
        
        update_payload = {
            "resume_data": current_data,
            "pdf_file_size": len(file_bytes),
            "pdf_url": f"{original_pdf_url}?t={int(time.time())}" # Initialize main view with original
        }
        await supabase_service.update_resume(resume_id, update_payload)
        
        return {"success": True, "message": "Original PDF uploaded successfully"}

        return {"success": True, "message": "Original PDF uploaded successfully"}

    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

class RefineRequest(BaseModel):
    resumeId: str
    selectedText: str
    userInstruction: str
    currentContext: str = ""
    sectionId: Optional[str] = None # Or jsonPath

@app.post("/api/v1/resume/refine", tags=["Resume Generation"])
async def refine_resume_text(
    payload: RefineRequest,
    user_id: str = Depends(get_current_user_id)
):
    """Refine a specific text block based on user instruction"""
    try:
        # 1. Verify ownership (Optional but good practice if we were saving directly)
        # For this lightweight endpoint, we might just process the text, but verifying ID exists is safer.
        
        # 2. Call AI Service
        refined_text = await ai_service.refine_text(
            payload.selectedText,
            payload.userInstruction,
            payload.currentContext
        )
        
        return {
            "success": True,
            "updatedText": refined_text,
            "sectionId": payload.sectionId
        }
        
    except Exception as e:
        logger.error(f"Refinement endpoint failed: {str(e)}")
        # Return success=False but don't crash - allow frontend to handle fallback
        return {"success": False, "error": str(e), "originalText": payload.selectedText}

@app.post("/api/v1/resumes/{resume_id}/enhance", tags=["Resumes"])
async def enhance_resume_endpoint(
    resume_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Trigger AI Enhancement (Strategist) on an existing resume"""
    try:
        # 1. Fetch Resume
        resume = await asyncio.wait_for(
            supabase_service.get_resume(resume_id),
            timeout=5.0
        )
        if not resume or resume.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # 2. Prepare Context (MERGE FRESH PROFILE DATA)
        # Fetch latest profile to ensure DOB/Nationality/Address are current
        user_profile = await supabase_service.get_user_by_id(user_id)
        
        current_data = resume.get("resume_data", {})
        
        # Merge Identity Fields if present in Profile but missing in Resume
        if user_profile:
            current_data["date_of_birth"] = user_profile.get("date_of_birth") or current_data.get("date_of_birth")
            current_data["nationality"] = user_profile.get("nationality") or current_data.get("nationality")
            current_data["street_address"] = user_profile.get("street_address") or current_data.get("street_address")
            current_data["postal_code"] = user_profile.get("postal_code") or current_data.get("postal_code")
            # Ensure contact city/country are synced if possible, but respect resume overrides
        
        job_description = resume.get("job_description", "")
        country = resume.get("country", "Germany")
        language = resume.get("language", "English")
        title = resume.get("title", current_data.get("job_title", "Professional"))

        logger.info(f"Enhancing resume {resume_id} for {title} in {country}")

        # --- VALIDATION STEP ---
        validation = ResumeComplianceValidator.validate(current_data, country)
        if not validation["valid"]:
            # We can either block or warn. 
            # For "compliance warning", sending back a 400 with details is standard 
            # if the frontend is set up to display them.
            # Assuming we want to BLOCK generation of invalid resumes for now to force compliance:
            error_messages = [e["message"] for e in validation["errors"]]
            raise HTTPException(
                status_code=400, 
                detail={
                    "message": "Resume compliance checks failed.",
                    "errors": validation["errors"],
                    "summary": " | ".join(error_messages)
                }
            )

        # 3. Request AI Generation (Step 2 Logic)
        generation_result = await ai_service.generate_tailored_resume(
            user_data=current_data,
            job_description=job_description,
            country=country,
            language=language,
            job_title=title,
            ats_report=current_data.get("ats_report")
        )

        if not generation_result.get("success"):
            raise HTTPException(status_code=500, detail=f"AI Enhancement Failed: {generation_result.get('error')}")

        # 4. Merge Results (Step 3 Logic)
        resume_text = generation_result["resume_content"]
        clean_summary = generation_result.get("generated_summary", "")
        spun_data = generation_result.get("spun_data", {})

        updated_data = {
            **current_data,
            "summary_text": resume_text,
            "professional_summary": clean_summary,
            "work_experiences": spun_data.get("work_experiences") or current_data.get("work_experiences", []),
            "skills": spun_data.get("skills") or current_data.get("skills", []),
            "educations": spun_data.get("educations") or current_data.get("educations", []),
            "projects": spun_data.get("projects") or current_data.get("projects", []),
            "headline": spun_data.get("headline") or current_data.get("headline", ""),
            "languages": spun_data.get("languages") or current_data.get("languages", []),
            "certifications": spun_data.get("certifications") or current_data.get("certifications", []),
            "links": spun_data.get("links") or current_data.get("links", []),
            "audit_log": generation_result.get("audit_log", {})
        }

        # 5. Regenerate HTML & PDF
        rag_data = RAGService.get_complete_rag(country, language)
        
        html_content = HTMLGenerator.generate_html(
            text=resume_text,
            full_name=updated_data.get("full_name", "Candidate"),
            contact_info=updated_data.get("contact", {}),
            user_data=updated_data,
            rag_data=rag_data,
            template_style=resume.get("template_style", "professional")
        )

        pdf_bytes = html_to_pdf(html_content)
        
        # 6. Upload & Save
        filename = f"{resume.get('slug')}.pdf"
        await supabase_service.upload_resume_pdf(user_id, resume_id, pdf_bytes, filename)
        
        # 7. Update DB
        update_payload = {
            "resume_data": updated_data,
            "pdf_file_size": len(pdf_bytes),
            "pdf_url": f"/api/v1/resume/{resume_id}/pdf" 
        }
        
        updated_resume = await supabase_service.update_resume(resume_id, update_payload)
        
        return {"success": True, "data": updated_resume}

    except Exception as e:
        logger.exception(f"Enhancement failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/v1/resumes/{resume_id}", tags=["Resumes"])
async def update_resume(
    resume_id: str, 
    data: dict, 
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user_ids)
):
    """Update resume metadata or content (triggers PDF regeneration and ATS scoring)"""
    from services.resume_service import resume_service
    
    try:
        # 1. Verify access
        resume = await asyncio.wait_for(
            supabase_service.get_resume(resume_id),
            timeout=5.0
        )
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found")
        
        # Check if user owns this resume (either ID matches)
        if resume.get("user_id") not in [user["platform_user_id"], user["auth_user_id"]]:
            raise HTTPException(status_code=403, detail="Not authorized")
            
        # 2. Update database
        logger.info(f"Updating resume {resume_id}...")
        regenerate = data.pop("regenerate_pdf", True)
        updated_resume = await asyncio.wait_for(
            supabase_service.update_resume(resume_id, data),
            timeout=10.0
        )
        
        # 3. Regenerate PDF if content (resume_data) was changed
        if regenerate and ("resume_data" in data or "template_style" in data or "title" in data):
            logger.info(f"Regenerating PDF for updated resume {resume_id}...")
            current_resume = updated_resume or resume
            r_data = current_resume.get("resume_data", {})
            
            # Generate HTML
            rag_data = RAGService.get_complete_rag(
                current_resume.get("country", "Germany"), 
                current_resume.get("language", "English")
            )
            
            html_content = HTMLGenerator.generate_html(
                text=r_data.get("summary_text", ""),
                full_name=r_data.get("full_name", "Candidate"),
                contact_info=r_data.get("contact", {}),
                user_data=r_data,
                rag_data=rag_data,
                template_style=current_resume.get("template_style", "professional")
            )
            
            # Convert to PDF
            pdf_bytes = html_to_pdf(html_content)
            
            # Upload PDF
            filename = f"{current_resume.get('slug') or resume_id}.pdf"
            await supabase_service.upload_resume_pdf(user_id, resume_id, pdf_bytes, filename)
            await supabase_service.update_resume(resume_id, {"pdf_file_size": len(pdf_bytes)})
            logger.info(f"PDF regeneration complete for {resume_id}")
            
            # 4. Run ATS analysis and save score IN BACKGROUND
            async def run_ats_task():
                logger.info(f"Background ATS analysis starting for {resume_id}...")
                try:
                    analysis = await asyncio.wait_for(
                        ai_service.analyze_resume(
                            r_data.get("summary_text", ""),
                            current_resume.get("title", "Resume"),
                            current_resume.get("country", "Germany"),
                            current_resume.get("job_description", "")
                        ),
                        timeout=60.0 # AI tasks can take longer
                    )
                    score = analysis.get("score", 0)
                    await asyncio.wait_for(
                        resume_service.save_score(resume_id, score, analysis),
                        timeout=10.0
                    )
                    
                    # Update resume_data with latest score
                    latest_resume = await asyncio.wait_for(
                        supabase_service.get_resume(resume_id),
                        timeout=5.0
                    )
                    latest_data = latest_resume.get("resume_data", {})
                    latest_data["score"] = score
                    await asyncio.wait_for(
                        supabase_service.update_resume(resume_id, {"resume_data": latest_data}),
                        timeout=10.0
                    )
                    
                    logger.info(f"Background ATS score {score} saved for {resume_id}")
                except Exception as e:
                    logger.warning(f"Background ATS analysis failed: {e}")

            background_tasks.add_task(run_ats_task)

        return {"success": True, "data": updated_resume}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/resumes/{resume_id}/clone", tags=["Resumes"])
async def clone_resume_endpoint(
    resume_id: str,
    data: dict,
    user_id: str = Depends(get_current_user_id)
):
    """Clone an existing resume"""
    from services.resume_service import resume_service
    
    try:
        # Verify ownership
        resume = await asyncio.wait_for(
            supabase_service.get_resume(resume_id),
            timeout=5.0
        )
        if not resume or resume.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        new_title = data.get("title")
        cloned = await asyncio.wait_for(
            resume_service.clone_resume(resume_id, new_title),
            timeout=15.0
        )
        
        return {"success": True, "data": cloned}
    except Exception as e:
        logger.error(f"Clone failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/resumes/{resume_id}/version", tags=["Resumes"])
async def create_version_endpoint(
    resume_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Create a version snapshot of the resume"""
    from services.resume_service import resume_service
    
    try:
        resume = await supabase_service.get_resume(resume_id)
        if not resume or resume.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        version = await resume_service.create_version(resume_id)
        return {"success": True, "data": version}
    except Exception as e:
        logger.error(f"Version creation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/resumes/{resume_id}/scores", tags=["Resumes"])
async def get_score_history_endpoint(
    resume_id: str,
    user_id: str = Depends(get_current_user_id),
    limit: int = 10
):
    """Get ATS score history for a resume"""
    from services.resume_service import resume_service
    
    try:
        resume = await supabase_service.get_resume(resume_id)
        if not resume or resume.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        scores = await resume_service.get_score_history(resume_id, limit)
        return {"success": True, "data": scores}
    except Exception as e:
        logger.error(f"Failed to get scores: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/resumes/{resume_id}/archive", tags=["Resumes"])
async def archive_resume_endpoint(
    resume_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Archive a resume (soft delete)"""
    from services.resume_service import resume_service
    
    try:
        resume = await supabase_service.get_resume(resume_id)
        if not resume or resume.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        success = await resume_service.archive_resume(resume_id)
        return {"success": success}
    except Exception as e:
        logger.error(f"Archive failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/resumes/{resume_id}/restore", tags=["Resumes"])
async def restore_resume_endpoint(
    resume_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Restore an archived resume"""
    from services.resume_service import resume_service
    
    try:
        success = await resume_service.restore_resume(resume_id)
        return {"success": success}
    except Exception as e:
        logger.error(f"Restore failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/v1/resumes/{resume_id}/default", tags=["Resumes"])
async def set_default_resume_endpoint(
    resume_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Set a resume as the user's default"""
    from services.resume_service import resume_service
    
    try:
        resume = await supabase_service.get_resume(resume_id)
        if not resume or resume.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        success = await resume_service.set_default_resume(user_id, resume_id)
        return {"success": success}
    except Exception as e:
        logger.error(f"Set default failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/resume/create", tags=["Resumes"])
async def create_new_resume(
    data: dict,
    user: Dict[str, Any] = Depends(get_current_user_ids)
):
    """Create a new resume with AI content generation"""
    user_id = user["platform_user_id"]
    auth_user_id = user["auth_user_id"]
    
    try:
        # PHASE 1: Stability & Rate-Limit Safety
        # 1a. Debounce (30s prevents double-submits)
        if user_id in ai_request_debounce_cache:
            logger.warning(f"Debounce triggered for user {user_id}")
            raise HTTPException(status_code=429, detail="Request already in progress. Please wait 30 seconds.")
        
        # 1b. Cooldown (90s after infrastructure failure)
        # if user_id in ai_service_cooldown_cache:
        #     logger.warning(f"Cooldown active for user {user_id}")
        #     raise HTTPException(status_code=503, detail="AI service is recovering. Please try again in a few moments.")

        # Set debounce
        ai_request_debounce_cache[user_id] = True

        # 1. Prepare user data context
        logger.info(f"Received resume creation request for user {user_id} (auth: {auth_user_id})")
        # 1. Fetch complete profile data to ensure all details are included
        logger.info(f"Fetching complete profile for user {user_id} (auth: {auth_user_id})...")
        db_profile = await profile_service.get_profile(auth_user_id)
        
        if not db_profile:
            logger.info(f"Profile not found by auth_id, trying platform_id {user_id}")
            db_profile = await profile_service.get_profile(user_id)
            
        # Merge request data with DB profile
        # Source of truth is DB profile for rich content, but request can override/supplement
        user_data = db_profile if db_profile else {}
        request_user_data = data.get("user_data", {})
        
        # Ensure contact info is structured correctly for HTMLGenerator and AI
        if "contact" not in user_data:
            user_data["contact"] = {
                "email": user_data.get("email", ""),
                "phone": user_data.get("phone", ""),
                "linkedin": user_data.get("linkedin_url", ""),
                "city": user_data.get("city", "")
            }
        
        # Merge basic overrides from request
        if request_user_data:
            # Explicit mapping for summary/professional_summary if needed
            if "summary" in request_user_data and "professional_summary" not in request_user_data:
                request_user_data["professional_summary"] = request_user_data["summary"]
            
            user_data.update(request_user_data)

        # ---------------------------------------------------------
        # COMPLIANCE VALIDATION (Phase 3 - German Market Gate)
        # ---------------------------------------------------------
        # ---------------------------------------------------------
        # COMPLIANCE VALIDATION (Phase 3 - German Market Gate)
        # ---------------------------------------------------------
        country = data.get("country", db_profile.get("country", "Germany") if db_profile else "Germany")
        
        # Allow user to explicitly skip compliance check (User Override)
        skip_compliance = data.get("skip_compliance", False)
        
        if not skip_compliance:
            validation_result = ResumeComplianceValidator.validate(user_data, country)
            if not validation_result["valid"]:
                logger.warning(f"Compliance validation failed for user {user_id}: {validation_result['errors']}")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "COMPLIANCE_ERROR",
                        "message": "Mandatory fields missing for this country.",
                        "errors": validation_result["errors"]
                    }
                )
        # ---------------------------------------------------------

        # 2. Add Job Context if provided

            # Re-ensure contact is structured if it was partially overridden
            if "contact" in request_user_data:
                user_data["contact"].update(request_user_data["contact"])
        
        # Log data summary for debugging
        logger.info(f"User data for AI: Name={user_data.get('full_name')}, "
                    f"Experiences={len(user_data.get('work_experiences', []))}, "
                    f"Education={len(user_data.get('educations', []))}")
        
        # 2. Extract input data
        job_description = data.get("job_description")
        
        # 3. Prepare resume payload structure
        resume_payload = {
            "title": data.get("title", "Untitled Resume"),
            "resume_data": user_data,
            "country": data.get("country", "Germany"),
            "language": data.get("language", "English"),
            "template_style": data.get("template_style", "professional"),
            "slug": data.get("slug") or f"resume-{uuid.uuid4().hex[:8]}",
            "job_description": job_description
        }
        
        logger.info(f"Creating resume row: {resume_payload['slug']}")
        # Step 1: Handle Job Title Authority
        job_title = data.get("job_title") or request_user_data.get("job_title")
        
        # DEBUG: Log input decision
        logger.info(f"Resume Creation - Job Title Input: '{job_title}' (Type: {type(job_title)})")
        
        # If job_title is provided in request, use it as the definitive resume title
        if job_title and str(job_title).strip():
            resume_payload["title"] = str(job_title).strip()
            logger.info(f"Prioritizing User Job Title: {resume_payload['title']}")
        elif not resume_payload.get("title") or resume_payload.get("title") == "Untitled Resume":
            # Fallback to smart title only if no job_title provided
            logger.info("No job_title provided, generating smart title...")
            smart_title = await ai_service.generate_resume_title(user_data, job_description)
            resume_payload["title"] = smart_title
        
        # Capture the effective title for analysis context
        effective_title = resume_payload["title"]

        # Check for skip flag (Direct Import Mode)
        skip_enhancement = data.get("skip_enhancement", False)
            
        # STEP 2: Generate Resume Content
        if skip_enhancement:
            logger.info("Skipping AI enhancement (Direct Import Mode). Using raw user_data.")
            tailored_data = resume_payload["resume_data"]
            
            # Step 2a: Set "resume_text" to the professional summary available in user_data
            # We do NOT generate HTML here. Step 3b will handle HTML generation.
            resume_text = tailored_data.get("professional_summary") or tailored_data.get("summary") or ""

            # Step 2b: Mock the "generation_result" structure expected by downstream logic (Step 3)
            # This prevents UnboundLocalError at line 1012
            generation_result = {
                "success": True,
                "resume_content": resume_text,
                "generated_summary": resume_text, # Use the raw summary
                "spun_data": { # No spinning happened, so we can leave this empty or mirror user_data
                    "work_experiences": tailored_data.get("work_experiences", []),
                    "skills": tailored_data.get("skills", []),
                    "educations": tailored_data.get("educations", []),
                    "projects": tailored_data.get("projects", []),
                    "headline": tailored_data.get("headline", ""),
                    "languages": tailored_data.get("languages", []),
                    "certifications": tailored_data.get("certifications", []),
                    "links": tailored_data.get("links", [])
                },
                "audit_log": {
                    "original_experience_count": len(tailored_data.get("work_experiences", [])),
                    "transformations": ["Skipped (Direct Import)"]
                }
            }
            logger.info("Direct Import Configured Successfully")

        else:
            # We pass the JD here so the AI can tailor the achievements
            logger.info(f"Step 2: Generating AI resume content (Tailored to JD: {bool(job_description)})")
            
            try:
                # CRITICAL: ALWAYS use the new Tailoring Engine (STEP A + STEP B)
                # Even without a JD, we need STEP A to generate an isolated, scope-correct summary
                logger.error("[MAIN] ENGAGING TAILORING ENGINE (STEP A + STEP B)")
                logger.error(f"[MAIN] Job Description Provided: {bool(job_description and len(job_description.strip()) > 10)}")
                
                generation_result = await ai_service.generate_tailored_resume(
                    user_data=resume_payload["resume_data"],
                    job_description=job_description or "",  # Pass empty string if no JD
                    country=resume_payload["country"],
                    language=resume_payload["language"],
                    job_title=effective_title # Use the effective title (user-provided or smart)
                )
            
                if generation_result.get("success"):
                    resume_text = generation_result["resume_content"]
                    resume_payload["resume_data"]["audit_log"] = generation_result["audit_log"]
                    logger.info("Tailoring Engine Success")
                else:
                    # CRITICAL: NO FALLBACK ALLOWED
                    # "If the AI fails to generate... FAIL the resume generation"
                    error_msg = generation_result.get("error", "Tailoring Engine failed")
                    details = generation_result.get("details", "")
                    logger.error(f"Tailoring Engine Failed: {error_msg} - {details}")
                    
                    if error_msg == "SUMMARY_GENERATION_FAILED":
                        # Policy Violation -> 400
                        await supabase_service.create_audit_log(
                            user_id=user_id,
                            action="RESUME_TAILORING_POLICY_FAILURE",
                            new_data=generation_result.get("audit_log", {})
                        )
                        raise HTTPException(status_code=400, detail="Resume tailoring failed due to strict AI policy.")

                    if error_msg == "SUMMARY_SCOPE_VIOLATION":
                        # Scope Violation -> 400
                        await supabase_service.create_audit_log(
                            user_id=user_id,
                            action="RESUME_TAILORING_SCOPE_FAILURE",
                            new_data=generation_result.get("audit_log", {})
                        )
                        raise HTTPException(status_code=400, detail=f"Resume tailoring failed: Summary scope violation ({details})")
                    
                    if error_msg == "AI_SERVICE_UNAVAILABLE":
                        # Infrastructure Failure -> 503
                        await supabase_service.create_audit_log(
                            user_id=user_id,
                            action="RESUME_TAILORING_INFRA_FAILURE",
                            new_data=generation_result.get("audit_log", {})
                        )
                        raise HTTPException(status_code=503, detail="AI service temporarily unavailable. Please retry.")

                    raise HTTPException(status_code=500, detail=f"Resume Generation Failed: {error_msg}")

            except HTTPException:
                # Re-raise HTTP exceptions directly (e.g., 400 for policy, 503 for infra)
                raise

            except Exception as ai_err:
                logger.exception(f"Step 2 FAILED: AI generation error: {ai_err}")
                raise HTTPException(status_code=503, detail=f"AI Content Generation failed: {str(ai_err)}")
            
        if not resume_text or "Generation failed" in resume_text:
            logger.error("Step 2 FAILED: AI returned failure message")
            raise HTTPException(status_code=503, detail="AI Content Generation failed to produce valid content")
            
        # Create the resume entry in the database *after* AI content is generated
        # This ensures we have the final title and content for the initial DB entry
        resume = await supabase_service.create_resume(user_id, resume_payload)
        resume_id = resume["id"]
        logger.info(f"Resume row created with ID: {resume_id}")

        # MERGED LOGIC MOVED UP
        clean_summary = generation_result.get("generated_summary")
        spun_data = generation_result.get("spun_data", {})

        if not clean_summary:
             logger.error("[MAIN] CRITICAL: generated_summary is MISSING from AI result. Using fallback.")
             clean_summary = ""

        updated_resume_data = {
            **resume_payload["resume_data"], 
            "summary_text": clean_summary, # FIX: Use clean summary instead of raw AI response to prevent duplicates
            "professional_summary": clean_summary,
            "work_experiences": spun_data["work_experiences"] if spun_data.get("work_experiences") is not None else resume_payload["resume_data"].get("work_experiences", []),
            "projects": spun_data["projects"] if spun_data.get("projects") is not None else resume_payload["resume_data"].get("projects", []),
            "skills": spun_data["skills"] if spun_data.get("skills") is not None else resume_payload["resume_data"].get("skills", []),
            "educations": spun_data["educations"] if spun_data.get("educations") is not None else resume_payload["resume_data"].get("educations", []),
            "certifications": spun_data.get("certifications") if spun_data.get("certifications") is not None else resume_payload["resume_data"].get("certifications", []),
            "motivation": spun_data.get("motivation") if spun_data.get("motivation") is not None else resume_payload["resume_data"].get("motivation", ""),
            "date_of_birth": spun_data.get("date_of_birth", resume_payload["resume_data"].get("date_of_birth")),
            "contact": spun_data.get("contact", resume_payload["resume_data"].get("contact")),
            # Fix Photo URL mismatch and pass Base64 fix
            "profile_pic_url": resume_payload["resume_data"].get("photo_url") or resume_payload["resume_data"].get("profile_pic_url"),
            "profile_pic_base64": spun_data.get("profile_pic_base64"),
            "summary": None, # Kill legacy
            "bio": None,     # Kill legacy
            "score": 0 # Placeholder, updated later
        }

        # --- Reliability Layer: Auto-Correction ---
        logger.info(f"Step 2c: Applying Reliability Layer (Auto-Correction) for {resume_payload['country']}...")
        updated_resume_data = resume_autocorrect.autocorrect_for_country(
            updated_resume_data, 
            resume_payload["country"]
        )

        logger.info(f"Step 3a: Fetching RAG for {resume_payload['country']}...")
        rag_data = RAGService.get_complete_rag(resume_payload["country"], resume_payload["language"])

        logger.info(f"Step 3b: Generating HTML content...")
        try:
            html_content = HTMLGenerator.generate_html(
                text=resume_text,
                full_name=resume_payload["resume_data"].get("full_name", "Candidate"),
                contact_info=resume_payload["resume_data"].get("contact", {}),
                user_data=updated_resume_data, # <--- KEY FIX: Use UPDATED data
                rag_data=rag_data,
                template_style=resume_payload["template_style"]
            )
        except Exception as html_err:
            logger.exception(f"HTML Generation Failed: {str(html_err)}")
            raise html_err
            
        # 3c. PDF Generation (Soft-Fail)
        logger.info(f"Step 3c: Converting HTML to PDF (xhtml2pdf)...")
        pdf_bytes = None
        try:
            pdf_bytes = html_to_pdf(html_content)
            logger.info(f"PDF generated for {resume_id} ({len(pdf_bytes)} bytes)")
        except Exception as pdf_err:
            logger.error(f"PDF Conversion Failed (Non-critical): {str(pdf_err)}")
            # Do NOT raise, allow process to continue so user gets their resume draft
            pdf_bytes = None

        # 4. Upload PDF to Storage (Only if generated)
        if pdf_bytes:
            filename = f"{resume['slug']}.pdf"
            logger.info(f"Uploading PDF to bucket resumes-pdf: {filename}")
            try:
                await supabase_service.upload_resume_pdf(user_id, resume_id, pdf_bytes, filename)
            except Exception as upload_err:
                logger.error(f"PDF Upload Failed: {upload_err}")
            
        # 5. Calculate Power Score using ATS analysis
        logger.info(f"Calculating ATS Power Score for {resume_id}...")
        try:
            # Use ai_service to analyze the newly generated resume text
            analysis = await ai_service.analyze_resume(
                resume_text, 
                effective_title, 
                resume_payload["country"], 
                job_description or ""
            )
            score = analysis.get("score", 70) 
        except Exception as e:
            logger.warning(f"ATS scoring failed for {resume_id}: {e}")
            score = 0
            
        updated_resume_data["score"] = score # Update score finally
        
        update_payload = {
            "resume_data": updated_resume_data,
            "pdf_file_size": len(pdf_bytes) if pdf_bytes else 0
        }
        
        # Only set URL if we actually generated a PDF
        if pdf_bytes:
            update_payload["pdf_url"] = f"/api/v1/resume/{resume_id}/pdf"
            
        updated_resume = await supabase_service.update_resume(resume_id, update_payload)
        logger.info(f"Resume {resume_id} update complete.")
        
        # Persist success audit log
        await supabase_service.create_audit_log(
            user_id=user_id,
            action="RESUME_TAILORING_SUCCESS",
            entity_type="resume",
            entity_id=resume_id,
            new_data=resume_payload["resume_data"].get("audit_log", {})
        )
        
        return {"success": True, "data": updated_resume or resume}
    except HTTPException as http_err:
        # Clear debounce on all errors (let cooldown handle infra blocks)
        if user_id in ai_request_debounce_cache:
            del ai_request_debounce_cache[user_id]
        raise http_err

    except Exception as e:
        # Clear debounce on all errors
        if user_id in ai_request_debounce_cache:
            del ai_request_debounce_cache[user_id]
        # KEY VARIANCE: Log traceback for "Blind" debugging
        import traceback
        logger.error(f"Tailoring Engine Failed: {str(e)}")
        
        # SNAPSHOT: Capture the raw context if possible
        try:
             # Try to dump the simple context
             context_snapshot = {
                "user_id": user_id,
                "job_title": resume_payload["title"] if "resume_payload" in locals() else "unknown",
                "request_data_keys": list(data.keys()) if "data" in locals() else []
             }
             logger.error(f"ENGINE_INPUT_DUMP: {json.dumps(context_snapshot)}")
        except:
             pass

        logger.error(traceback.format_exc()) # CRITICAL DEBUG LINE
        
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        # Final safety to ensure debounce is eventually cleared
        # In a real app, TTL handles this, but we can be proactive
        pass

@app.get("/api/v1/resume/{resume_id}/pdf", tags=["Resumes"])
async def download_resume_pdf(
    resume_id: str,
    request: Request,
    inline: bool = False,
    preview: bool = False, # New param to identify iframe views
    skip_logging: bool = False, # New param for manual frontend logging
    user_id: Optional[str] = Depends(get_optional_user_id)
):
    """Proxied download of resume PDF from storage"""
    try:
        # 1. Verify access
        resume = await supabase_service.get_resume(resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found")
        
        # In a real app we'd check if resume.user_id == user_id, 
        # but for now let's allow public access if the resume is public or it's the owner
        if resume.get('user_id') != user_id and resume.get('visibility') != 'public':
             raise HTTPException(status_code=403, detail="Access denied")

        # 2. Get path from storage
        # Path structure: {user_id}/{resume_id}/{resume['slug']}.pdf
        effective_user_id = resume.get('user_id')
        slug_filename = f"{resume.get('slug')}.pdf" # File ON DISK is still slug-based
        path = f"{effective_user_id}/{resume_id}/{slug_filename}"
        
        logger.info(f"Proxied download request: {path} (Preview: {preview}, SkipLog: {skip_logging})")
        
        # 3. Download from Supabase using service client
        file_data = await supabase_service.client.storage.from_("resumes-pdf").download(path)
        
        # 4. Construct User-Friendly Filename: "Name - Title.pdf"
        resume_data = resume.get('resume_data', {})
        user_data = resume_data.get('user_data', {})
        full_name = user_data.get('full_name', 'Resume')
        job_title = resume.get('title', 'Document')
        
        import re
        def sanitize(s):
            return re.sub(r'[<>:"/\\|?*]', '', str(s)).strip()
            
        display_filename = f"{sanitize(full_name)} - {sanitize(job_title)}.pdf"

        disposition_type = "inline" if inline else "attachment"

        # 5. Log Download (Server-Side Reliability)
        # Filter out Range requests, Preview requests, and Manual Logging requests
        range_header = request.headers.get("range")
        is_partial = range_header and "bytes=0-" not in range_header 
        
        async def stream_with_logging():
            # Yield the file content
            chunk_size = 1024 * 1 
            
            try:
                for i in range(0, len(file_data), chunk_size):
                    yield file_data[i:i + chunk_size]
                    await asyncio.sleep(0.01) 
                    
                # IF WE GET HERE -> Stream finished successfully
                if not is_partial and not preview and not skip_logging:
                    try:
                         # Prepare payload
                        download_payload = {
                             "visitor_ip": request.client.host if request.client else "127.0.0.1",
                             "device_type": "Desktop" if "Windows" in request.headers.get("user-agent", "") else "Mobile",
                             "session_id": "direct-download",
                             "visitor_country": "Unknown" 
                        }
                        # LOG IT
                        await supabase_service.log_resume_download(resume_id, download_payload)
                        logger.info(f"Download stream completed & logged for {resume_id}")
                    except Exception as log_e:
                        logger.error(f"Post-stream logging failed: {log_e}")
                        
            except Exception as stream_e:
                logger.warning(f"Download stream interrupted/cancelled for {resume_id}: {stream_e}")
                # Do NOT log download if interrupted

        return StreamingResponse(
            stream_with_logging(),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"{disposition_type}; filename=\"{display_filename}\""
            }
        )
    except Exception as e:
        logger.error(f"Proxied download failed for {resume_id}: {str(e)}")
        # If it's a 404 from Supabase, propagate it
        if "Object not found" in str(e) or "Bucket not found" in str(e):
             raise HTTPException(status_code=404, detail="PDF file not found in storage")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/profile/completion", tags=["Profile"])
async def get_profile_completion(user_id: str = Depends(get_current_user_id)):
    """Get profile completion percentage"""
    try:
        percentage = profile_service.get_profile_completion_percentage(user_id)
        return {"completion_percentage": percentage}
    except Exception as e:
        logger.error(f"Error calculating profile completion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------
# Pydantic Models
# -----------------------------
class ContactInfo(BaseModel):
    email: EmailStr
    phone: str = Field(..., pattern=r'^\+?[1-9]\d{1,14}$')
    street_address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    links: Optional[List[Dict[str, str]]] = []

class Experience(BaseModel):
    title: str
    company: str
    city: Optional[str] = None
    country: Optional[str] = None
    location: Optional[str] = None # Legacy field
    start_date: str
    end_date: Optional[str] = None
    description: Union[str, List[str]]

class Project(BaseModel):
    title: str
    role: Optional[str] = None
    link: Optional[str] = None
    description: Optional[str] = None
    technologies: List[str] = []
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class Education(BaseModel):
    degree: str
    institution: str
    city: Optional[str] = None
    country: Optional[str] = None
    graduation_date: Optional[str] = None
    gpa: Optional[float] = None

class UserData(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    headline: Optional[str] = None
    date_of_birth: Optional[str] = None
    nationality: Optional[str] = None
    contact: ContactInfo
    summary: Optional[str] = None
    experience: List[Experience] = []
    projects: List[Project] = []
    education: List[Education] = []
    skills: List[str] = []
    certifications: Optional[List[str]] = []
    links: Optional[List[Dict[str, str]]] = []
    # Accept either simple strings or structured objects { name: str, level: str }
    languages: Optional[List[Union[str, Dict[str, str]]]] = ["English"]
    ats_report: Optional[Dict[str, Any]] = None

    @validator('skills')
    def validate_skills(cls, v):
        if len(v) > 50:
            raise ValueError('Maximum 50 skills allowed')
        return v

class ResumeRequest(BaseModel):
    country: str
    language: str = "English"
    user_data: Optional[UserData] = None
    template_style: Optional[str] = Field("executive", pattern=r'^(professional|modern|minimal|creative|vibrant|executive)$')

class JobMatchRequest(BaseModel):
    user_data: Dict[str, Any]
    language: str = "English"
    location: Optional[str] = None
    job_type: Optional[str] = Field("full-time", pattern=r'^(full-time|part-time|contract|internship)$')
    remote_preference: Optional[bool] = False

class APIResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

# -----------------------------
# Utility Functions
# -----------------------------
def create_error_response(request: Request, error: str, status_code: int = 500) -> APIResponse:
    return APIResponse(success=False, error=error, request_id=getattr(request.state, "request_id", None))

def create_success_response(request: Request, data: Any) -> APIResponse:
    return APIResponse(success=True, data=data, request_id=getattr(request.state, "request_id", None))

def sanitize_filename(filename: str) -> str:
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w\-_\. ]', '', filename)
    return filename[:100]

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key != Config.API_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key

# -----------------------------
# AI Service
# -----------------------------
# ai_service is imported from services.ai_service


# -----------------------------
# HTML Generator
# -----------------------------
class HTMLGenerator:
    _env = Environment(loader=FileSystemLoader("templates"))

    @staticmethod
    def generate_html(text: str, full_name: str, contact_info: dict, user_data: dict, rag_data: dict, template_style: str = "professional") -> str:
        """
        Generate HTML resume using Jinja2 templates and RAG data.
        """
        template_name = f"resume_{template_style}.jinja2"
        
        try:
            template = HTMLGenerator._env.get_template(template_name)
        except Exception as e:
            logger.warning(f"Template '{template_name}' not found, falling back to professional. Error: {e}")
            template = HTMLGenerator._env.get_template("resume_professional.jinja2")

        # SANITIZATION: Clean text fields to prevent xhtml2pdf "word gaps" bug
        def clean_field(t):
            if not t or not isinstance(t, str): return t
            return " ".join(t.split())

        user_data["full_name"] = clean_field(user_data.get("full_name"))
        if user_data.get("headline"):
            user_data["headline"] = clean_field(user_data["headline"])

        # SANITIZATION: Ensure data types are PDF-safe (xhtml2pdf is strict)
        # 1. Ensure lists are actually lists (AI sometimes returns strings)
        if user_data.get("work_experiences"):
            for job in user_data["work_experiences"]:
                if isinstance(job.get("description"), str):
                     job["description"] = [job["description"]]
                if isinstance(job.get("achievements"), str):
                     job["achievements"] = [job["achievements"]]
                # Ensure dates are strings
                if job.get("start_date") and not isinstance(job.get("start_date"), str):
                    job["start_date"] = str(job["start_date"])
                if job.get("end_date") and not isinstance(job.get("end_date"), str):
                    job["end_date"] = str(job["end_date"])

        if user_data.get("projects"):
             for proj in user_data["projects"]:
                if isinstance(proj.get("description"), str):
                     proj["description"] = proj["description"] # Description is usually a string in projects, KEEP IT
                # But wait, the template handles string description: <p>{{ proj.description }}</p>
                # Check technologies
                if isinstance(proj.get("technologies"), str):
                     proj["technologies"] = [t.strip() for t in proj["technologies"].split(",")]

        # 2. Ensure skills is a list and sanitize
        if isinstance(user_data.get("skills"), str):
            user_data["skills"] = [s.strip() for s in user_data["skills"].split(",")]
        
        if user_data.get("skills"):
            user_data["skills"] = [s for s in user_data["skills"] if isinstance(s, str) and not re.search(r'^https?://|^mailto:|@|\.com\b|\.in\b', s.lower())]

        # 3. Sanitize languages
        if user_data.get("languages") and isinstance(user_data["languages"], list):
            sanitized_langs = []
            for lang in user_data["languages"]:
                if isinstance(lang, str):
                    if re.search(r'^https?://|^mailto:|@|\.com\b|\.in\b', lang.lower()):
                        continue
                sanitized_langs.append(lang)
            user_data["languages"] = sanitized_langs

        # Ensure photo data is valid for PDF generator
        if user_data.get("profile_pic_base64"):
             # Base64 is reliable for PDF engines, so we prioritize it
             logger.info("Using embedded Base64 photo for PDF generation.")
        elif user_data.get("profile_pic_url"):
             logger.info("Using remote photo URL (May fail in some PDF engines).")

        # Render template with structured data and RAG knowledge
        return template.render(
            user_data=user_data,
            rag_data=rag_data,
            text=text,  # Kept for backward compat or raw text block use
            full_name=full_name, # Redundant but safe
            contact_info=contact_info # Redundant but safe
        )


# -----------------------------
# Helper for background tasks
# -----------------------------
def log_generation_event(user_identifier: str, event_type: str, extra_data: dict = None):
    """
    Minimal placeholder for generation event logging used by background tasks.
    """
    try:
        log_entry = {
            "user": user_identifier,
            "event": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "details": extra_data or {}
        }
        logger.info(f"[Generation Event] {log_entry}")
    except Exception as e:
        logger.error(f"Failed to log generation event: {e}")

# -----------------------------
# Save resume helper
# -----------------------------
def _save_resume_and_upload_pdf_sync(user_row: Dict[str, Any], resume_metadata: Dict[str, Any], pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Uses methods from services.supabase_service to:
      - create resume row
      - upload pdf
      - update resume row with pdf_url and file size
    Returns final resume row (dict).
    """
    client = supabase_service.client
    user_id = user_row.get("id")

    try:
        insert_resp = client.table("resumes").insert({
            "user_id": user_id,
            "slug": resume_metadata.get("slug"),
            "title": resume_metadata.get("title") or f"{user_row.get('full_name','Resume')}",
            "resume_data": resume_metadata.get("resume_data", {}),
            "country": resume_metadata.get("country"),
            "language": resume_metadata.get("language"),
            "template_style": resume_metadata.get("template_style"),
            "visibility": resume_metadata.get("visibility"),
            "is_default": resume_metadata.get("is_default", False),
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        if isinstance(insert_resp, dict):
            resume_row = insert_resp.get("data")[0] if insert_resp.get("data") else None
        else:
            resume_row = getattr(insert_resp, "data", None)
            if isinstance(resume_row, list):
                resume_row = resume_row[0] if resume_row else None
        if not resume_row:
            raise Exception("Failed to create resume row")
    except Exception as e:
        logger.exception(f"Failed to create resume row: {e}")
        raise HTTPException(status_code=500, detail="Failed to create resume")

    resume_id = resume_row.get("id")
    filename = sanitize_filename(f"{resume_row.get('slug') or resume_id}.pdf")

    try:
        upload_resp = supabase_service.upload_resume_pdf(user_id, resume_id, pdf_bytes, filename)
        if hasattr(upload_resp, "__await__"):
            # Requires imports: import asyncio (added at top)
            pdf_url = asyncio.get_event_loop().run_until_complete(upload_resp)
        else:
            pdf_url = upload_resp
    except Exception as e:
        logger.exception(f"Failed to upload PDF: {e}")
        try:
            client.table("resumes").delete().eq("id", resume_id).execute()
            logger.info("Rolled back resume row after upload failure")
        except Exception:
            logger.warning("Could not rollback resume row")
        raise HTTPException(status_code=500, detail="Failed to upload PDF to storage")

    try:
        client.table("resumes").update({
            "pdf_url": pdf_url,
            "pdf_file_size": len(pdf_bytes)
        }).eq("id", resume_id).execute()
    except Exception as e:
        logger.exception(f"Failed to update resume row with pdf_url: {e}")

    resume_row.update({"pdf_url": pdf_url, "pdf_file_size": len(pdf_bytes)})
    return resume_row

@app.get("/api/v1/auth/check-username/{username}", tags=["Auth"])
async def check_username_availability(username: str, request: Request):
    """Check if a username is available"""
    try:
        available = await supabase_service.check_username_available(username)
        return {
            "success": True,
            "data": {
                "username": username,
                "available": available
            }
        }
    except Exception as e:
        logger.exception(f"Username check failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/api/v1/resume", tags=["Resume Generation"])
@limiter.limit("10/minute")
async def generate_resume(
    request: Request,
    resume_req: ResumeRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key)
):
    """Legacy behavior (keeps returning PDF binary)"""
    try:
        if not resume_req.user_data:
            raise HTTPException(status_code=400, detail="user_data is required")

        user_data = resume_req.user_data.dict()
        if not user_data.get("full_name"):
            raise HTTPException(status_code=400, detail="full_name is required")
        if not user_data.get("contact", {}).get("email"):
            raise HTTPException(status_code=400, detail="contact.email is required")

        resume_text = await AIService.generate_resume_text(
            user_data,
            resume_req.country,
            resume_req.language,
            resume_req.template_style
        )

        html_content = HTMLGenerator.generate_html(
            text=resume_text,
            full_name=user_data.get("full_name", "Candidate"),
            contact_info=user_data.get("contact", {}),
            template_style=resume_req.template_style
        )

        # Convert HTML to PDF using xhtml2pdf
        pdf_bytes = html_to_pdf(html_content)

        background_tasks.add_task(
            lambda *args, **kwargs: log_generation_event(user_data.get("full_name"), "resume_generated", {
                "country": resume_req.country,
                "language": resume_req.language,
                "template_style": resume_req.template_style
            })
        )

        safe_filename = sanitize_filename(f"{user_data.get('full_name', 'resume')}.pdf")

        # Return PDF binary stream (legacy clients expect this)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={safe_filename}",
                "X-Request-ID": request.state.request_id
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Resume generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------
# Profile Endpoints
# -----------------------------
@app.post("/api/v1/profile/upload-photo", tags=["Profile"])
async def upload_profile_photo(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id)
):
    """Upload user profile picture to Supabase Storage"""
    try:
        # Validate file
        safe_filename = FileProcessor.validate_file(file) 
        ext = os.path.splitext(safe_filename)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
            raise HTTPException(status_code=400, detail="Only images allowed (jpg, png, webp)")
        
        # Create unique filename: user_id/timestamp.ext
        file_path = f"{user_id}/{int(datetime.utcnow().timestamp())}{ext}"
        
        # Read file content
        contents = await file.read()
        
        # Upload to Supabase 'profile-pictures' bucket
        bucket_name = "profile-pictures"
        
        # Upload using Supabase Service Client (Bypasses RLS issues)
        resp = supabase_service.client.storage.from_(bucket_name).upload(
            file_path, 
            contents, 
            {"content-type": file.content_type, "upsert": "true"}
        )
        
        # Construct Public URL
        public_url = supabase_service.client.storage.from_(bucket_name).get_public_url(file_path)
        
        # Update User Profile
        await profile_service.create_or_update_profile(user_id, {"photo_url": public_url})
        
        return {"success": True, "photo_url": public_url}

    except Exception as e:
        logger.error(f"Error uploading photo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


# -----------------------------
# ATS Analysis Endpoints
# -----------------------------

class ATSAnalysisRequest(BaseModel):
    resume_text: str
    job_role: str
    target_country: str
    job_description: Optional[str] = ""

@app.post("/api/v1/ats/analyze", tags=["ATS"])
async def analyze_resume_ats(
    file: UploadFile = File(...),
    job_role: str = Form(...),
    target_country: str = Form(...),
    job_description: Optional[str] = Form("")
):
    """Analyze resume against ATS standards from an uploaded file"""
    logger.info(f"Received ATS analysis request for role: {job_role}, country: {target_country}")
    try:
        # Validate and parse file
        filename = FileProcessor.validate_file(file)
        ext = os.path.splitext(filename)[1].lower()
        
        result = {}
        if ext == '.pdf':
            # Pass ai_service for OCR fallback
            result = await FileProcessor.parse_pdf(file, ai_service)
        elif ext == '.docx':
            result = FileProcessor.parse_docx(file)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format")
            
        resume_text = result["text"]
        if not resume_text:
            raise HTTPException(status_code=400, detail="Could not extract text from file")
            
        metadata = result.get("metadata", {})
        warnings = result.get("warnings", [])
        
        # Prepend metadata to resume text so AI sees it
        from textwrap import dedent
        meta_header = dedent(f"""
        [METADATA START]
        Detected Page Count: {metadata.get('page_count', 'Unknown')}
        Contains Photos/Images: {metadata.get('has_images', False)} (If True, DO NOT complain about missing photo)
        File Type: {metadata.get('file_type', 'Unknown')}
        [METADATA END]
        """).strip()
        
        full_context_text = meta_header + "\n\n" + resume_text

        # Analyze using AI
        # PARALLEL EXECUTION: Get Analysis AND Structured Data for "Edit & Improve" flow
        import asyncio
        analysis_task = ai_service.analyze_resume(
            full_context_text,
            job_role,
            target_country,
            job_description,
            parsing_warnings=warnings
        )
        extraction_task = ai_service.extract_structured_data(resume_text)
        
        analysis, structured_data = await asyncio.gather(analysis_task, extraction_task)
        
        # Attach extracted data to response so frontend can use it for "Edit & Improve"
        analysis["debug_parsed_profile"] = structured_data
        
        return {"success": True, "data": analysis}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------
# Run server
# -----------------------------
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on {Config.HOST}:{Config.PORT}")
    logger.info(f"Database: Supabase ({'configured' if Config.SUPABASE_URL else 'missing'})")
    uvicorn.run(
        "main:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=True,
        log_level="info"
    )