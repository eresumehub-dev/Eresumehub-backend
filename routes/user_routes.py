from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
import logging
import asyncio
from utils.auth_deps import get_current_user_id
from services.supabase_service import supabase_service
from services.profile_service import ProfileService
from app_settings import Config

router = APIRouter(prefix="/api/v1/user", tags=["User Orchestration"])
logger = logging.getLogger(__name__)
profile_service = ProfileService(supabase_service)

@router.get("/bootstrap")
async def get_user_dashboard_bootstrap(user_id: str = Depends(get_current_user_id)):
    """
    The 'Golden Bootstrap' Endpoint (v16.2.0 Alignment)
    Gather Profile, Analytics, and Resumes keyed to the Auth UUID ID.
    """
    try:
        # Performance Tier: Parallel Fetching
        try:
            data = await asyncio.wait_for(
                profile_service.get_dashboard_bootstrap(user_id),
                timeout=Config.AI_REQUEST_TIMEOUT 
            )
            return {"success": True, "data": data}
            
        except asyncio.TimeoutError:
            logger.error(f"Bootstrap fetch timed out for user {user_id}")
            raise HTTPException(status_code=504, detail="Upstream request timeout")
            
    except Exception as e:
        logger.error(f"Bootstrap API failure: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
