from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from typing import Dict, Any
import os
import json
import logging
from datetime import datetime

from services.supabase_service import supabase_service
from services.profile_service import ProfileService
from services.ai_service import ai_service
from services.rag_service import RAGService
from utils.auth_deps import get_current_user_id
from utils.file_processor import FileProcessor

# Initialize router
router = APIRouter(prefix="/api/v1/profile", tags=["Profile"])

# Initialize service
profile_service = ProfileService(supabase_service)

logger = logging.getLogger(__name__)
 
@router.get("")
@router.get("/")
async def get_profile(user_id: str = Depends(get_current_user_id)):
    """Fetch user profile with work experience and education"""
    try:
        profile = await profile_service.get_profile(user_id)
        if not profile:
            # Return a structured empty profile instead of 404 to gracefully handle new users
            return {
                "exists": False,
                "profile": {
                    "full_name": "",
                    "email": "",
                    "professional_summary": "",
                    "work_experiences": [],
                    "educations": [],
                    "skills": [],
                    "languages": [],
                    "projects": [],
                    "certifications": [],
                    "extras": {}
                }
            }
        return {"exists": True, "profile": profile}
    except Exception as e:
        logger.error(f"Error fetching profile: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch profile: {str(e)}")

@router.post("")
@router.post("/")
@router.put("")
async def update_profile(
    profile_data: Dict[str, Any],
    user_id: str = Depends(get_current_user_id)
):
    """Create or update user profile manually"""
    try:
        profile = await profile_service.create_or_update_profile(user_id, profile_data)
        return {"success": True, "profile": profile}
    except Exception as e:
        logger.error(f"Error updating profile: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")

@router.post("/from-resume")
async def create_profile_from_resume(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id)
):
    """Parse uploaded resume and create/update profile from it"""
    try:
        from app_settings import Config
        import asyncio
        
        # Parse resume text
        safe_filename = FileProcessor.validate_file(file)
        ext = os.path.splitext(safe_filename)[1].lower()
        
        try:
            if ext == '.pdf':
                parsed_result = await FileProcessor.parse_pdf(file)
                text = parsed_result.get("text", "")
            elif ext == '.docx':
                parsed_result = await FileProcessor.parse_docx(file)
                text = parsed_result.get("text", "")
            else:
                text = (await file.read()).decode('utf-8')
        except Exception as parse_e:
            logger.error(f"File parsing failed: {parse_e}")
            raise HTTPException(status_code=400, detail="Could not read the uploaded file.")
            
        # 1. AI EXTRACTION with Timeout Safety (v6.1.0)
        try:
            structured_data = await asyncio.wait_for(
                ai_service.extract_structured_data(text),
                timeout=Config.AI_REQUEST_TIMEOUT 
            )
        except asyncio.TimeoutError:
            logger.error(f"AI Extraction timed out for user {user_id}")
            raise HTTPException(status_code=504, detail="AI extraction timed out. Please try again.")
        
        # 2. SCHEMA TRANSFORMATION (Strict v6.3.0 logic)
        # We no longer raise 422 here because ai_service.extract_structured_data (v6.3.0) 
        # now guarantees a 'full_name' fallback if AI fails.
        if not structured_data:
            structured_data = {"full_name": "Resume Professional", "work_experiences": [], "educations": []}

        # Map to platform profile schema
        profile_data = {
            "full_name": structured_data.get("full_name", "Resume Professional"),
            "headline": structured_data.get("headline", ""),
            "email": structured_data.get("email", ""),
            "phone": structured_data.get("phone", ""),
            "city": structured_data.get("city", "Berlin"), # Default or AI extracted
            "country": structured_data.get("country", "Germany"),
            "professional_summary": structured_data.get("summary", ""),
            "skills": structured_data.get("skills", []),
            "work_experiences": structured_data.get("work_experiences") or structured_data.get("experience", []),
            "educations": structured_data.get("educations") or structured_data.get("education", []),
            "projects": structured_data.get("projects", []),
            "certifications": structured_data.get("certifications", []),
            "languages": structured_data.get("languages", [])
        }
        
        # 3. SERVICE PERSISTENCE (Safe v6.1.0 logic)
        try:
            profile = await asyncio.wait_for(
                profile_service.create_or_update_profile(user_id, profile_data, is_ai_import=True),
                timeout=Config.AI_REQUEST_TIMEOUT 
            )
        except asyncio.TimeoutError:
             logger.error(f"Profile creation timed out for user {user_id}")
             raise HTTPException(status_code=504, detail="Database update timed out.")
        
        logger.info(f"RESUME IMPORT SUCCESS: User {user_id} ({profile_data.get('full_name')})")
        
        return {
            "success": True,
            "profile": profile,
            "message": "Resume imported successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"FAILSAFE: 500 Error during resume import: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred during resume import.")

@router.post("/upload-photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id)
):
    """Upload user profile picture to Supabase Storage"""
    try:
        # Validate file
        safe_filename = FileProcessor.validate_file(file) 
        ext = os.path.splitext(safe_filename)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
            raise HTTPException(status_code=400, detail="Only images allowed (jpg, png, webp)")
        
        # Read file content
        contents = await file.read()
        
        # Upload using Supabase Service (which handles bucket creation/publicity)
        photo_url = await supabase_service.upload_profile_picture(user_id, contents, f"{int(datetime.utcnow().timestamp())}{ext}")
        
        # Update User Profile with new photo URL
        await profile_service.create_or_update_profile(user_id, {"photo_url": photo_url})
        
        return {"success": True, "photo_url": photo_url}

    except Exception as e:
        logger.error(f"Error uploading photo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@router.get("/completion")
async def get_profile_completion(user_id: str = Depends(get_current_user_id)):
    """Get profile completion percentage"""
    try:
        percentage = await profile_service.get_profile_completion_percentage(user_id)
        return {"completion_percentage": percentage}
    except Exception as e:
        logger.error(f"Error calculating profile completion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-summary")
async def generate_profile_summary_endpoint(
    profile_data: Dict[str, Any],
    user_id: str = Depends(get_current_user_id)
):
    """Staff+ AI: Generate professional summary from profile context."""
    try:
        from services.profile_service import ProfileService
        summary = await profile_service.generate_summary(profile_data)
        return {"summary": summary}
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate summary")
