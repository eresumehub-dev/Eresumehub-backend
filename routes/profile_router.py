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

@router.post("/from-resume")
async def create_profile_from_resume(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id)
):
    """Parse uploaded resume and create/update profile from it"""
    try:
        # Parse resume
        safe_filename = FileProcessor.validate_file(file)
        ext = os.path.splitext(safe_filename)[1].lower()
        
        if ext == '.pdf':
            parsed_result = await FileProcessor.parse_pdf(file)
            text = parsed_result.get("text", "")
        elif ext == '.docx':
            parsed_result = await FileProcessor.parse_docx(file)
            text = parsed_result.get("text", "")
        elif ext == '.txt':
             content = await file.read()
             text = content.decode('utf-8')
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
        
        # Extract structured data using AI (Corrected to use instance method)
        structured_data = await ai_service.extract_structured_data(text)
        
        # SAFETY CHECK: If AI fails to return data, do NOT proceed (Prevents wiping profile)
        if not structured_data or not structured_data.get("full_name"):
            logger.error(f"AI Extraction failed or returned empty results for user {user_id}")
            raise HTTPException(
                status_code=500, 
                detail="AI failed to extract information from the resume. Profile was NOT cleared. Please try again or fill manually."
            )

        # Convert to profile format
        profile_data = {
            "full_name": structured_data.get("full_name", ""),
            "headline": structured_data.get("headline", ""),
            "email": structured_data.get("email", ""),
            "phone": structured_data.get("phone", ""),
            "city": structured_data.get("city", ""),
            "country": structured_data.get("country", ""),
            "street_address": structured_data.get("street_address", ""),
            "postal_code": structured_data.get("postal_code", ""),
            "nationality": structured_data.get("nationality", ""),
            "date_of_birth": structured_data.get("date_of_birth", ""),
            "professional_summary": structured_data.get("summary", ""),
            "skills": structured_data.get("skills", []),
            "work_experiences": structured_data.get("experience", []),
            "projects": structured_data.get("projects", []),
            "educations": structured_data.get("education", []),
            "certifications": structured_data.get("certifications", []),
            "links": structured_data.get("links", []),
            "languages": [
                {"name": lang, "level": "Native"} if isinstance(lang, str) else lang 
                for lang in structured_data.get("languages", [])
            ]
        }
        
        # Create/update profile
        profile = await profile_service.create_or_update_profile(user_id, profile_data)
        
        return {
            "success": True,
            "profile": profile,
            "message": "Profile created from resume successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating profile from resume: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

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
