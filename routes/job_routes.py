from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Dict, Any, Optional
import logging
from rq.job import Job
from utils.auth_deps import get_current_user_ids
from schemas.resume_schemas import JobStatusResponse
from fastapi.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/v1", tags=["Jobs"])
logger = logging.getLogger(__name__)

async def _fetch_job_safely(job_id: str, redis_conn) -> Job:
    """Offload blocking RQ fetch to threadpool (Staff+ Performance)."""
    return await run_in_threadpool(Job.fetch, job_id, connection=redis_conn)

@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, request: Request, user: Dict[str, Any] = Depends(get_current_user_ids)):
    """Async status polling with ownership validation & security shielding."""
    request_id = getattr(request.state, "request_id", "unknown")
    try:
        if not hasattr(request.app.state, "redis") or not request.app.state.redis:
            raise HTTPException(status_code=503, detail="Job system unavailable")
            
        # ⚡ 1. Offload Blocking Fetch (Staff+ Performance)
        job = await _fetch_job_safely(job_id, request.app.state.redis)
        
        # 🔒 2. Distributed-Safe Ownership Validation (Staff+ Security)
        user_id = str(user["platform_user_id"])
        job_meta = job.meta or {}
        job_owner_id = job_meta.get("user_id")
        
        # Guard: Ensure IDs are compared as strings (Redis often returns bytes)
        if job_owner_id and str(job_owner_id) != user_id:
            logger.warning(f"[{request_id}] FORBIDDEN: User {user_id} tried to poll job {job_id} owned by {job_owner_id}")
            raise HTTPException(status_code=403, detail="Unauthorized access to this job")
            
        # ⚡ 3. Direct Property Access (Staff+ Optimization)
        # These are in-memory once Job.fetch() completes - no threadpool needed.
        status = job.get_status()
        is_finished = job.is_finished
        is_failed = job.is_failed
        
        # 🛡️ 4. API Error Sanitization (Security Shield)
        # Never leak raw exc_info to the client.
        error_msg = None
        if is_failed:
            # Map structured pipeline errors if available, otherwise generic
            job_result = job.result if isinstance(job.result, dict) else {}
            error_msg = job_result.get("error") or "Background task failed. Please try again."
            logger.error(f"[{request_id}] Job {job_id} FAILED: {job.exc_info}")

        return {
            "job_id": job_id,
            "status": status,
            "progress": job_meta.get("progress", 0),
            "step": job_meta.get("step", "Processing..."),
            "result": job.result if is_finished else None,
            "error": error_msg,
            "request_id": request_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Job fetch failed for {job_id}: {e}")
        raise HTTPException(status_code=404, detail="Job not found or expired")
