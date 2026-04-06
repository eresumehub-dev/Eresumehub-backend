from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Dict, Any
from utils.auth_deps import get_current_user_id
import logging

router = APIRouter(prefix="/api/v1/system", tags=["System"])
logger = logging.getLogger(__name__)

@router.get("/stats")
async def get_system_stats(request: Request, user_id: str = Depends(get_current_user_id)):
    """Elite Operational Visibility: Real-time generation-pipeline monitoring (Synchronous Native)."""
    # 1. Connectivity Check
    if not hasattr(request.app.state, "redis") or not request.app.state.redis:
        return {
            "success": False,
            "status": "OFFLINE",
            "error": "Redis connection unavailable"
        }
        
    redis_conn = request.app.state.redis
    
    try:
        # 2. Metrics Ticker
        # We store these as strings in Redis to keep the stats light
        total_jobs = await redis_conn.get("metrics:jobs:total")
        success_jobs = await redis_conn.get("metrics:jobs:success")
        
        return {
            "success": True,
            "status": "HEALTHY",
            "infrastructure": {
                "redis": "ONLINE",
                "workers": "DEPRECATED (Synchronous Architecture)",
                "queues": "DEPRECATED (Synchronous Architecture)"
            },
            "performance": {
                "total_runs": int(total_jobs or 0),
                "success_rate": f"{round((int(success_jobs or 0) / int(total_jobs or 1)) * 100, 2)}%" if total_jobs else "0%"
            }
        }
    except Exception as e:
        logger.error(f"System stats fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch system metrics")
