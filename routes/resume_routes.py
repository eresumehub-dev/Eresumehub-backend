from fastapi import APIRouter, Request, HTTPException, Depends, UploadFile, File, Form, status
from typing import List, Optional, Dict, Any, Union
import uuid
import time
import logging
from rq import Retry
import hashlib
import json
import hmac
from app_settings import Config
from services.supabase_service import supabase_service
from services.resume_service import resume_service
from services.resume_pipeline import ResumePipeline, run_pipeline_job
from schemas.resume_schemas import (
    CreateResumeRequest, UpdateResumeRequest, RefineRequest, APIResponse
)
from routes.auth import get_current_user_id, get_current_user_ids

router = APIRouter(prefix="/api/v1", tags=["Resumes"])
logger = logging.getLogger(__name__)

from fastapi.concurrency import run_in_threadpool

@router.post("/resume/create", response_model=Dict[str, Any])
async def create_new_resume(
    request: Request,
    data: CreateResumeRequest,
    user: Dict[str, Any] = Depends(get_current_user_ids)
):
    """Create a new resume with AI content generation & Idempotent Deduplication (Staff+ Elite)."""
    user_id = user["platform_user_id"]
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    
    if not hasattr(request.app.state, "rq_queue") or not request.app.state.rq_queue:
        raise HTTPException(status_code=503, detail="Background worker system is offline")

    # 1. Generate Idempotency Key (SHA-256 of User + Action + Payload)
    # This prevents expensive AI generations if the user spams the button
    payload_json = json.dumps(data.model_dump(), sort_keys=True)
    idempotency_hash = hashlib.sha256(f"{user_id}:create:{payload_json}".encode()).hexdigest()
    idempotency_key = f"idempotency:{idempotency_hash}"
    
    # 2. Check for existing job (Idempotent Hit)
    existing_job_id = await request.app.state.redis.get(idempotency_key)
    if existing_job_id:
        logger.info(f"[{request_id}] Idempotent HIT for user {user_id}. Returning existing job {existing_job_id}")
        return {"success": True, "job_id": existing_job_id.decode() if isinstance(existing_job_id, bytes) else existing_job_id, "idempotent": True}

    # 3. Distributed Debounce (Coarse Lock)
    debounce_key = f"debounce:create:{user_id}"
    if await request.app.state.redis.get(debounce_key):
        raise HTTPException(status_code=429, detail="Request already in progress.")
    
    # 4. Enqueue Job with Threadpool Safety
    try:
        # Atomic debounce for 30s
        await request.app.state.redis.setex(debounce_key, 30, "1")
        
        job_data = data.model_dump()
        job_data["action"] = "create"
        job_id = f"job:{user_id}:{uuid.uuid4()}"
        
        # Staff+ Safety: Offload the blocking Redis/RQ '.enqueue()' call
        # Route to High-Priority queue for user-facing generation
        job = await run_in_threadpool(
            request.app.state.high_queue.enqueue,
            run_pipeline_job,
            args=(request_id, user, job_data),
            job_id=job_id,
            result_ttl=3600,
            job_timeout=300,
            retry=Retry(max=3, interval=[10, 30, 60]),
            meta={"user_id": user_id, "request_id": request_id, "idempotency_key": idempotency_key}
        )
        
        # 5. Store Idempotency Key (expires in 5 mins)
        await request.app.state.redis.setex(idempotency_key, 300, job_id)
        
        return {"success": True, "job_id": job.get_id()}
    except Exception as e:
        logger.exception(f"[{request_id}] Resume creation failed: {e}")
        await request.app.state.redis.delete(debounce_key)
        await request.app.state.redis.delete(idempotency_key)
        raise HTTPException(status_code=500, detail="Internal server error")
    # Debounce is NOT popped here - it will be popped by the worker or TTL

@router.post("/resume/improve")
async def improve_existing_resume(
    request: Request,
    file: UploadFile = File(...),
    country: str = Form("Germany"),
    job_description: str = Form(""),
    user = Depends(get_current_user_ids)
):
    """Staff+ Hardened Resume Improvement Flow."""
    from utils.file_processor import FileProcessor
    import os
    
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    user_id = user["platform_user_id"]
    
    if not hasattr(request.app.state, "rq_queue") or not request.app.state.rq_queue:
        raise HTTPException(status_code=503, detail="Worker system offline")

    # 2. Distributed Debounce
    debounce_key = f"debounce:improve:{user_id}"
    if await request.app.state.redis.get(debounce_key):
        raise HTTPException(status_code=429, detail="Analysis in progress.")
    
    try:
        await request.app.state.redis.setex(debounce_key, 30, "1")
        
        # 3. Parse (AWAITED fix)
        safe_filename = FileProcessor.validate_file(file)
        ext = os.path.splitext(safe_filename)[1].lower()
        
        if ext == '.pdf':
            result = await FileProcessor.parse_pdf(file)
        elif ext == '.docx':
            result = FileProcessor.parse_docx(file)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
            
        text = result["text"]
        
        # 4. Offloaded Enqueue to High-Priority Queue
        job = await run_in_threadpool(
            request.app.state.high_queue.enqueue,
            run_pipeline_job,
            args=(request_id, user, {
                "action": "improve",
                "resume_text": text,
                "country": country,
                "job_description": job_description
            }),
            job_id=f"improve_{user_id}_{uuid.uuid4()}",
            result_ttl=3600,
            job_timeout=300,
            retry=Retry(max=3, interval=[10, 30, 60]),
            meta={"user_id": user_id, "request_id": request_id}
        )
        
        return {"success": True, "job_id": job.get_id()}
    except HTTPException:
        # Explicitly re-raise to preserve status codes (v3.12.0 fix)
        await request.app.state.redis.delete(debounce_key)
        raise
    except Exception as e:
        logger.error(f"Improvement failed: {e}")
        await request.app.state.redis.delete(debounce_key)
        raise HTTPException(status_code=500, detail="Internal server error")

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
