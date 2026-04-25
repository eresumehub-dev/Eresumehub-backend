from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Dict, Any, Optional
import logging
from utils.auth_deps import get_current_user_id
from schemas.resume_schemas import JobStatusResponse

router = APIRouter(prefix="/api/v1", tags=["Jobs"])
logger = logging.getLogger(__name__)

@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, request: Request, user_id: str = Depends(get_current_user_id)):
    """Fetch job status from Supabase (v16.5.0)."""
    request_id = getattr(request.state, "request_id", "unknown")
    try:
        from services.supabase_service import supabase_service
        job = await supabase_service.get_job(job_id)
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
            
        # 🔒 Ownership Validation
        if str(job.get("user_id")) != user_id:
            logger.warning(f"[{request_id}] FORBIDDEN: User {user_id} tried to poll job {job_id} owned by {job.get('user_id')}")
            raise HTTPException(status_code=403, detail="Unauthorized access to this job")
            
        return {
            "job_id": job_id,
            "status": job.get("status"),
            "progress": job.get("progress", 0),
            "step": job.get("step", "Processing..."),
            "result": job.get("result") if job.get("status") == "completed" else None,
            "error": job.get("error"),
            "request_id": request_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Job fetch failed for {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
