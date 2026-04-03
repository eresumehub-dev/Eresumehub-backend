from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Request
from typing import Dict, Any, Optional
import logging
import asyncio
from utils.auth_deps import get_current_user_id
from services.supabase_service import supabase_service
from services.profile_service import ProfileService

from app_settings import Config
router = APIRouter(prefix="/api/v1/profile", tags=["Profile"])
logger = logging.getLogger(__name__)
profile_service = ProfileService(supabase_service)

@router.get("")
async def get_user_profile_endpoint(user_id: str = Depends(get_current_user_id)):
    """Get user's complete profile using canonical auth_user_id."""
    try:
        # Use a safety timeout to prevent hanging the event loop (Identified in review)
        try:
            profile = await asyncio.wait_for(
                profile_service.get_profile(user_id),
                timeout=Config.AI_REQUEST_TIMEOUT 
            )
        except asyncio.TimeoutError:
            logger.error(f"Profile fetch timed out for user {user_id}")
            return {"profile": None, "exists": False, "error": "Database timeout"}
            
        if not profile:
            # Return a structured empty profile to prevent frontend crashes
            return {
                "profile": {
                    "full_name": "",
                    "headline": "",
                    "summary": "",
                    "skills": [],
                    "contact": {"email": "", "phone": "", "location": ""},
                    "experience": [],
                    "education": []
                },
                "exists": False
            }
        return {"profile": profile, "exists": True}
    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        # Shield internal errors from client (Identified in review)
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("")
async def create_or_update_profile_endpoint(
    profile_data: dict,
    user_id: str = Depends(get_current_user_id)
):
    """Create or update user profile with transaction safety (Auth UUID)."""
    try:
        profile = await asyncio.wait_for(
            profile_service.create_or_update_profile(user_id, profile_data),
            timeout=Config.AI_REQUEST_TIMEOUT 
        )
        return {"success": True, "profile": profile}
    except Exception as e:
        logger.error(f"Profile update failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update profile")

@router.post("/photo")
async def upload_profile_photo_endpoint(
    file: UploadFile = File(...),
    user = Depends(get_current_user_ids)
):
    """Staff+ Production-Ready Profile Photo Upload."""
    try:
        from services.supabase_service import supabase_service
        from uuid import uuid4
        user_id = user["platform_user_id"]
        # 1. Validate Meta BEFORE Read (Staff+ Performance)
        from utils.file_processor import FileProcessor
        FileProcessor.validate_file(file) 
        
        # 2. Consume stream
        contents = await file.read()
        
        # 3. Preserve original extension (v3.12.0 fix)
        ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "jpg"
        filename = f"profile-{user_id}-{uuid4().hex[:8]}.{ext}"
        
        # 4. Upload & Persist
        photo_url = await supabase_service.upload_profile_picture(user_id, contents, filename)
        await supabase_service.update_user(user_id, {"profile_image_url": photo_url})
        
        return {"success": True, "photo_url": photo_url}
    except Exception as e:
        logger.error(f"Profile photo upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload photo")
