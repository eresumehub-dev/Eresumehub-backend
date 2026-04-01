from fastapi import FastAPI, Request, HTTPException, Depends, Header, UploadFile, File, Form, BackgroundTasks, Query, status
# Force Reload Timestamp: 2026-02-16 16:35 Fixed Imports
from fastapi.responses import JSONResponse, StreamingResponse
# Reload trigger 2026-01-07 Reloaded for Gemini v1 stability
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, EmailStr, field_validator as validator
from dotenv import load_dotenv
import os
import io
import json
import time
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
from services.resume_pipeline import ResumePipeline
from utils.pdf_utils import html_to_pdf
from utils.html_generator import HTMLGenerator

# Initialize Profile Service
profile_service = ProfileService(supabase_service)
analytics_service = AnalyticsService(supabase_service)

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
is_wildcard = "*" in Config.ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    # If wildcard is used, we must use allow_origin_regex to support allow_credentials=True
    # safely while conforming to browser security models.
    allow_origins=[] if is_wildcard else Config.ALLOWED_ORIGINS,
    allow_origin_regex=".*" if is_wildcard else None,
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
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    user_id = user["platform_user_id"]
    
    try:
        pipeline = ResumePipeline(request_id, profile_service)
        result = await pipeline.run_enhancement(user_id, resume_id)
        
        return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[{request_id}] Enhancement failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/v1/resumes/{resume_id}", tags=["Resumes"])
async def update_resume(
    request: Request,
    resume_id: str, 
    data: dict, 
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user_ids)
):
    """Update resume metadata or content (triggers PDF regeneration and ATS scoring)"""
    from services.resume_service import resume_service
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    user_id = user["platform_user_id"]
    
    try:
        # 1. Verify access
        resume = await supabase_service.get_resume(resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found")
        
        # Check if user owns this resume
        if resume.get("user_id") not in [user["platform_user_id"], user["auth_user_id"]]:
            raise HTTPException(status_code=403, detail="Not authorized")
            
        # 2. Update database
        logger.info(f"[{request_id}] Updating resume {resume_id}...")
        regenerate = data.pop("regenerate_pdf", True)
        updated_resume = await supabase_service.update_resume(resume_id, data)
        
        # 3. Regenerate PDF if content was changed
        if regenerate and ("resume_data" in data or "template_style" in data or "title" in data):
            logger.info(f"[{request_id}] Regenerating PDF for updated resume {resume_id}...")
            current_resume = updated_resume or resume
            r_data = current_resume.get("resume_data", {})
            
            # Use utility abstractions instead of raw logic
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
            
            # Convert to PDF in threadpool
            pdf_bytes = await run_in_threadpool(html_to_pdf, html_content)
            
            # Upload PDF
            filename = f"{current_resume.get('slug') or resume_id}.pdf"
            await supabase_service.upload_resume_pdf(user.get("id") or user["platform_user_id"], resume_id, pdf_bytes, filename)
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
    request: Request,
    data: CreateResumeRequest,
    user: Dict[str, Any] = Depends(get_current_user_ids)
):
    """Create a new resume with AI content generation using the ResumePipeline"""
    user_id = user["platform_user_id"]
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    
    try:
        # Check Debounce
        if user_id in ai_request_debounce_cache:
            logger.warning(f"[{request_id}] Debounce triggered for user {user_id}")
            raise HTTPException(status_code=429, detail="Request already in progress.")
        
        ai_request_debounce_cache[user_id] = True

        # Call the new architected pipeline!
        pipeline = ResumePipeline(request_id, profile_service)
        result = await pipeline.run(user, data.model_dump())
        
        return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[{request_id}] Resume creation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # CRITICAL FIX: Guarantee debounce clearance 
        ai_request_debounce_cache.pop(user_id, None)

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

# Duplicate route @app.get("/api/v1/profile/completion") removed

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
    @classmethod
    def validate_skills(cls, v):
        if len(v) > 50:
            raise ValueError('Maximum 50 skills allowed')
        return v

class ResumeRequest(BaseModel):
    country: str
    language: str = "English"
    user_data: Optional[UserData] = None
    template_style: Optional[str] = Field("executive", pattern=r'^(professional|modern|minimal|creative|vibrant|executive)$')

class CreateResumeRequest(BaseModel):
    user_data: Optional[Dict[str, Any]] = None
    job_description: Optional[str] = None
    job_title: Optional[str] = None
    country: str = "Germany"
    language: str = "English"
    template_style: str = "professional"
    slug: Optional[str] = None
    skip_compliance: bool = False
    skip_enhancement: bool = False

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
# HTMLGenerator is now in utils/html_generator.py
# End of Resume Helpers section

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

        user_data = resume_req.user_data.model_dump()
        if not user_data.get("full_name"):
            raise HTTPException(status_code=400, detail="full_name is required")
        if not user_data.get("contact", {}).get("email"):
            raise HTTPException(status_code=400, detail="contact.email is required")

        # FIX: Use instance 'ai_service' and offload CPU-bound PDF generation
        resume_text = await ai_service.generate_resume_text(
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

        pdf_bytes = await run_in_threadpool(html_to_pdf, html_content)

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
# Duplicate route @app.post("/api/v1/profile/upload-photo") removed


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