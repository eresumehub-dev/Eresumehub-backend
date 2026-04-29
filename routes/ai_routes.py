from fastapi import APIRouter, Depends, HTTPException, Body
from typing import Dict, Any
from services.ai_service import ai_service
from utils.auth_deps import get_current_user_id
import logging

router = APIRouter(prefix="/api/v1/ai", tags=["AI Intelligence"])
logger = logging.getLogger(__name__)

@router.post("/generate-motivation")
async def generate_motivation(
    payload: Dict[str, Any] = Body(...),
    user_id: str = Depends(get_current_user_id)
):
    """
    Generate an AI-powered motivation draft (Shi-bo-do-ki).
    Gated to authenticated users only.
    """
    user_data = payload.get("user_data")
    job_title = payload.get("job_title")
    country = payload.get("country", "Japan")

    if not user_data or not job_title:
        raise HTTPException(status_code=400, detail="Missing user_data or job_title")

    try:
        draft = await ai_service.generate_motivation_draft(user_data, job_title, country)
        if not draft:
            raise HTTPException(status_code=500, detail="AI generation failed")
        
        return {
            "success": True,
            "draft": draft
        }
    except Exception as e:
        logger.error(f"Motivation generation route failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
