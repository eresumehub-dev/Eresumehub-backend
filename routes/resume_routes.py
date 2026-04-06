from fastapi import APIRouter, Request, HTTPException, Depends, UploadFile, File, Form, status
from typing import List, Optional, Dict, Any, Union
import uuid
import time
import logging
import hashlib
import json
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
from services.analytics_service import analytics_service

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
    # We pass the canonical Auth UUID exclusively to prevent FK mismatches.
    user_ctx = {"auth_user_id": user_id}

    # 2. Generate Idempotency Key (v16.4.1 Serialization Safety)
    # Using model_dump_json() ensures Pydantic types (EmailStr, UUID) are handled correctly.
    payload_json = data.model_dump_json()
    idempotency_hash = hashlib.sha256(f"{user_id}:create:{payload_json}".encode()).hexdigest()
    idempotency_key = f"idempotency:{idempotency_hash}"
    
    # 3. Infrastructure Guard (v16.3.2 Alignment)
    # If Redis is offline, we must NOT crash with AttributeError.
    if not hasattr(request.app.state, "redis") or not request.app.state.redis:
        logger.error(f"[{request_id}] CRITICAL: Redis is OFFLINE. Proceeding with 503 fail-fast.")
        raise HTTPException(status_code=503, detail="Idempotency engine is offline. Please try again in 30s.")

    # 3b. Check for existing job (Idempotent Hit)
    existing_job_id = await request.app.state.redis.get(idempotency_key)
    if existing_job_id:
        logger.info(f"[{request_id}] Idempotent HIT for user {user_id}. Returning existing job {existing_job_id}")
        return {"success": True, "job_id": existing_job_id.decode() if isinstance(existing_job_id, bytes) else existing_job_id, "idempotent": True}

    # 3. Distributed Debounce
    debounce_key = f"debounce:create:{user_id}"
    if await request.app.state.redis.get(debounce_key):
        raise HTTPException(status_code=429, detail="Request already in progress.")
    
    # 4. Synchronous Execution (v16.4.9 Pivot)
    start_time = time.time()
    try:
        await request.app.state.redis.setex(debounce_key, 60, "1") # 60s lock for sync flow
        
        job_data = data.model_dump()
        job_data["action"] = "create"
        
        # 🔑 Direct Pipeline Call (No Worker Tier)
        result = await ResumePipeline.run_for_user(
            request_id=request_id,
            profile_service=ProfileService(supabase_service),
            ai_service=ai_service,
            supabase_service=supabase_service,
            analytics_service=analytics_service,
            user=user_ctx,
            data=job_data
        )
        
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"[{request_id}] Resume created SYNCHRONOUSLY in {elapsed:.2f}ms")
        
        # Clean up debounce lock immediately on success
        await request.app.state.redis.delete(debounce_key)
        
        return {
            "success": True, 
            "data": result["data"],
            "id": result["resume_id"]
        }
    except Exception as e:
        logger.exception(f"[{request_id}] Synchronous resume creation failed: {e}")
        await request.app.state.redis.delete(debounce_key)
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")

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
    import os
    
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    user_ctx = {"auth_user_id": user_id}
    
    debounce_key = f"debounce:improve:{user_id}"
    if await request.app.state.redis.get(debounce_key):
        raise HTTPException(status_code=429, detail="Analysis in progress.")
    
    try:
        await request.app.state.redis.setex(debounce_key, 30, "1")
        
        safe_filename = FileProcessor.validate_file(file)
        ext = os.path.splitext(safe_filename)[1].lower()
        
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
            analytics_service=analytics_service,
            user=user_ctx,
            data={
                "action": "improve",
                "resume_text": text,
                "country": country,
                "job_description": job_description
            }
        )
        
        await request.app.state.redis.delete(debounce_key)
        return {"success": True, "data": result["data"], "id": result["resume_id"]}
    except HTTPException:
        await request.app.state.redis.delete(debounce_key)
        raise
    except Exception as e:
        logger.error(f"Synchronous improvement failed: {e}")
        await request.app.state.redis.delete(debounce_key)
        raise HTTPException(status_code=500, detail=f"Improvement failed: {str(e)}")

@router.get("/resumes", response_model=Dict[str, Any])
async def list_user_resumes(request: Request, user_id: str = Depends(get_current_user_id)):
    """Fetch all resumes belonging to the authenticated user (Auth UUID)."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    try:
        resumes = await supabase_service.get_user_resumes(user_id)
        return {"success": True, "data": {"resumes": resumes}}
    except Exception as e:
        logger.exception(f"[{request_id}] Resume generation request failed: {e}")
        # Staff+ Transparency: Temporary str(e) for rapid field diagnosis
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")

@router.get("/resumes/{resume_id}")
async def get_resume_endpoint(resume_id: str, user_id: str = Depends(get_current_user_id)):
    resume = await supabase_service.get_resume(resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
        
    # 403 Forbidden for ownership violations (Identified in review)
    if resume.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized access to this resume")
        
    return {"success": True, "data": resume}

@router.delete("/resumes/{resume_id}")
async def delete_resume_endpoint(resume_id: str, user_id: str = Depends(get_current_user_id)):
    resume = await supabase_service.get_resume(resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
        
    if resume.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    success = await resume_service.delete_resume(resume_id)
    return {"success": success}

@router.post("/resumes/{resume_id}/restore")
async def restore_resume_endpoint(resume_id: str, user_id: str = Depends(get_current_user_id)):
    resume = await supabase_service.get_resume(resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
        
    if resume.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to restore this resume")
        
    success = await resume_service.restore_resume(resume_id)
    return {"success": success}

@router.post("/resume/refine")
async def refine_resume_text(
    payload: RefineRequest,
    user_id: str = Depends(get_current_user_id)
):
    """Refine a specific text block based on user instruction."""
    from services.ai_service import ai_service
    try:
        refined_text = await ai_service.refine_text(
            payload.selectedText,
            payload.userInstruction,
            payload.currentContext
        )
        return {"success": True, "updatedText": refined_text, "sectionId": payload.sectionId}
    except Exception as e:
        logger.error(f"Refinement failed: {e}")
        raise HTTPException(status_code=500, detail="Refinement failed")

@router.post("/ats/scan")
async def scan_resume_ats(
    file: UploadFile = File(...),
    job_description: str = Form(""),
    user_id: str = Depends(get_current_user_id)
):
    """Deep scan resume for ATS compatibility with Staff+ async hardening."""
    from utils.file_processor import FileProcessor
    from services.ai_service import ai_service
    try:
        content = await FileProcessor.parse_pdf(file)
        analysis = await ai_service.analyze_resume(
            content["text"], 
            "Resume Scan", 
            "Germany", 
            job_description
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        logger.error(f"ATS Scan failed: {e}")
        raise HTTPException(status_code=500, detail="ATS Scan failed")

@router.get("/metrics/jobs")
async def get_pipeline_metrics(request: Request, user_id: str = Depends(get_current_user_id)):
    """Elite Observability: Fetch real-time pipeline performance metrics."""
    # Staff+ Security: Role Gating (v3.12.0)
    # TODO: Implement full RBAC check against platform_user roles
    # Currently restricted via session check + internal staff-secret validation
    staff_key = request.headers.get("X-Staff-Secret")
    if not staff_key or not hmac.compare_digest(staff_key, Config.API_SECRET_KEY):
        raise HTTPException(status_code=403, detail="Staff access required for metrics.")

    # Staff+ Check: Ensure redis state is active
    if not hasattr(request.app.state, "redis") or not request.app.state.redis:
        raise HTTPException(status_code=503, detail="Metrics engine offline")
        
    redis = request.app.state.redis
    total = await redis.get("metrics:jobs:total")
    success = await redis.get("metrics:jobs:success")
    failed = await redis.get("metrics:jobs:failed")
    
    # Latency: Get last 50 and average them
    latencies = await redis.lrange("metrics:jobs:latency", 0, 49)
    avg_latency = 0
    if latencies:
        # Staff+ Safety: Decode bytes if Redis returns them
        latencies_decoded = [float(l.decode() if isinstance(l, bytes) else l) for l in latencies]
        avg_latency = sum(latencies_decoded) / len(latencies_decoded)
        
    total_int = int(total or 0)
    
    return {
        "success": True,
        "metrics": {
            "total_jobs": total_int,
            "success_rate": f"{round((int(success or 0) / total_int) * 100, 2)}%" if total_int > 0 else "0%",
            "failure_rate": f"{round((int(failed or 0) / total_int) * 100, 2)}%" if total_int > 0 else "0%",
            "average_latency_seconds": round(avg_latency, 2)
        },
        "request_id": getattr(request.state, "request_id", "unknown")
    }
