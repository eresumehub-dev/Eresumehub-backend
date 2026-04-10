from fastapi import APIRouter, Request, HTTPException, Depends, UploadFile, File, Form, status
from typing import List, Optional, Dict, Any, Union
import uuid
import time
import logging
import hashlib
import json
import os
import hmac
from app_settings import Config
from services.supabase_service import supabase_service
from services.resume_service import resume_service
from services.resume_pipeline import ResumePipeline
from services.profile_service import ProfileService
from services.ai_service import ai_service
from schemas.resume_schemas import (
    CreateResumeRequest, UpdateResumeRequest, RefineRequest, APIResponse
)
from utils.auth_deps import get_current_user_id, get_current_user_ids

router = APIRouter(prefix="/api/v1", tags=["Resumes"])
logger = logging.getLogger(__name__)

from fastapi.concurrency import run_in_threadpool

@router.post("/resume/create", response_model=Dict[str, Any])
async def create_new_resume(
    request: Request,
    data: CreateResumeRequest,
    user_id: str = Depends(get_current_user_id)
):
    """Create a new resume with AI content generation & Idempotent Deduplication (Auth UUID)."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    
    # 1. Pipeline Identity Context (v16.4.0 Clean Identity)
    user_ctx = {"auth_user_id": user_id}

    # 2. Generate Idempotency Key
    payload_json = data.model_dump_json()
    idempotency_hash = hashlib.sha256(f"{user_id}:create:{payload_json}".encode()).hexdigest()
    idempotency_key = f"idempotency:{idempotency_hash}"
    
    # Check Infrastructure
    if not hasattr(request.app.state, "redis") or not request.app.state.redis:
        logger.error(f"[{request_id}] CRITICAL: Redis is OFFLINE.")
        raise HTTPException(status_code=503, detail="Idempotency engine is offline.")

    # 3b. Check for existing job (Idempotent Hit)
    existing_job_id = await request.app.state.redis.get(idempotency_key)
    if existing_job_id:
        return {"success": True, "job_id": existing_job_id.decode() if isinstance(existing_job_id, bytes) else existing_job_id, "idempotent": True}

    # 3. Distributed Debounce
    debounce_key = f"debounce:create:{user_id}"
    if await request.app.state.redis.get(debounce_key):
        raise HTTPException(status_code=429, detail="Request already in progress.")
        
    # --- PROACTIVE COMPLIANCE GATE ---
    unif_ignore = getattr(data, 'ignore_compliance', False) or getattr(data, 'ignoreCompliance', False)
    
    if not unif_ignore:
        # Dynamic Schema Loading
        c_lower = data.country.lower()
        schema = None
        rag_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rag_schemas")
        schema_path = os.path.join(rag_dir, c_lower, "knowledge_base.json")
        if os.path.exists(schema_path):
            try:
                with open(schema_path, 'r', encoding='utf-8') as f:
                    schema = json.load(f)
            except: pass
        
        if schema:
            cv_structure = schema.get("cv_structure", {})
            req_info = cv_structure.get("mandatory_sections", {}).get("personal_info", {}).get("required", [])
            req_info_lower = [r.lower() for r in req_info]
            
            if any("date of birth" in r or "dob" in r for r in req_info_lower):
                if not data.user_data.date_of_birth or not data.user_data.date_of_birth.strip():
                    raise HTTPException(status_code=422, detail={"status": "requires_user_action", "message": f"{data.country} standard CVs strictly require a Date of Birth."})
            
            if any("nationality" in r for r in req_info_lower):
                if not data.user_data.nationality or not data.user_data.nationality.strip():
                    raise HTTPException(status_code=422, detail={"status": "requires_user_action", "message": f"Nationality is mandatory in {data.country}."})

    # 4. Synchronous Execution
    start_time = time.time()
    try:
        await request.app.state.redis.setex(debounce_key, 60, "1")
        
        job_data = data.model_dump()
        job_data["action"] = "create"
        
        result = await ResumePipeline.run_for_user(
            request_id=request_id,
            profile_service=ProfileService(supabase_service),
            ai_service=ai_service,
            supabase_service=supabase_service,
            analytics_service=request.app.state.analytics_service,
            user=user_ctx,
            data=job_data
        )

        await request.app.state.redis.delete(debounce_key)
        return {
            "success": True, 
            "data": result["data"],
            "id": result["resume_id"],
            "compliance_gap": result.get("compliance_gap", [])
        }
    except Exception as e:
        logger.exception(f"[{request_id}] Generation failed: {e}")
        await request.app.state.redis.delete(debounce_key)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/resume/improve")
async def improve_existing_resume(
    request: Request,
    file: UploadFile = File(...),
    country: str = Form("Germany"),
    job_description: str = Form(""),
    user_id: str = Depends(get_current_user_id)
):
    """Staff+ Hardened Resume Improvement Flow (Auth UUID)."""
    from utils.file_processor import FileProcessor
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    user_ctx = {"auth_user_id": user_id}
    
    debounce_key = f"debounce:improve:{user_id}"
    if await request.app.state.redis.get(debounce_key):
        raise HTTPException(status_code=429, detail="Request already in progress.")

    try:
        await request.app.state.redis.setex(debounce_key, 60, "1")
        
        # 1. Parse File
        ext = os.path.splitext(file.filename)[1].lower()
        if ext == '.pdf':
            result = await FileProcessor.parse_pdf(file)
        elif ext == '.docx':
            result = FileProcessor.parse_docx(file)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
            
        text = result["text"]
        
        # 🔑 Direct Pipeline Call (v16.4.9)
        result = await ResumePipeline.run_for_user(
            request_id=request_id,
            profile_service=ProfileService(supabase_service),
            ai_service=ai_service,
            supabase_service=supabase_service,
            analytics_service=request.app.state.analytics_service,
            user=user_ctx,
            data={
                "action": "improve",
                "resume_text": text,
                "country": country,
                "job_description": job_description
            }
        )
        
        await request.app.state.redis.delete(debounce_key)
        return {
            "success": True, 
            "data": result["improved_text"],  # Canonical key for frontend
            "id": result["resume_id"]
        }
    except HTTPException:
        await request.app.state.redis.delete(debounce_key)
        raise
    except Exception as e:
        logger.error(f"Improvement failed: {e}")
        await request.app.state.redis.delete(debounce_key)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/resumes", response_model=Dict[str, Any])
async def list_user_resumes(request: Request, user_id: str = Depends(get_current_user_id)):
    """Fetch all resumes belonging to the authenticated user."""
    resumes = await supabase_service.get_user_resumes(user_id)
    return {"success": True, "data": resumes}

@router.get("/resume/{resume_id}")
async def get_resume_detail(resume_id: str, user_id: str = Depends(get_current_user_id)):
    """Fetch details for a specific resume."""
    resume = await supabase_service.get_resume(resume_id)
    if not resume or resume.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Resume not found")
    return {"success": True, "data": resume}

@router.delete("/resume/{resume_id}")
async def delete_resume(resume_id: str, user_id: str = Depends(get_current_user_id)):
    """Soft delete a resume."""
    success = await supabase_service.delete_resume(resume_id)
    return {"success": success}
