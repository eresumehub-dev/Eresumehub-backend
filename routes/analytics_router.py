from fastapi import APIRouter, Depends, HTTPException, Body, Request
from services.supabase_service import supabase_service
from services.analytics_service import AnalyticsService
from typing import Dict, Any, Optional

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])
analytics_service = AnalyticsService(supabase_service)

# Dependency to get current user ID (assuming it's available from main or auth)
# For now, we'll redefine a simple dependency or import it if we restructure, 
# but to keep it self-contained we can pass the user_id in the body or header if checking auth.
# ideally we import get_current_user_id from main, but that causes circular imports.
# So we will rely on the main.py to protect the dashboard endpoint.

from fastapi import APIRouter, Depends, HTTPException, Body, Request

@router.post("/view")
async def log_view(request: Request, view_data: Dict[str, Any] = Body(...)):
    """Log a view start and return view_id"""
    try:
        # Inject visitor IP if not present
        if not view_data.get("visitor_ip"):
            if request.client:
                view_data["visitor_ip"] = request.client.host
            else:
                 view_data["visitor_ip"] = "127.0.0.1" # Fallback for local/proxy

        # Sanitize payload: Empty strings crash UUID columns in Postgres
        for key, value in view_data.items():
            if value == "":
                view_data[key] = None

        resume_id = view_data.get("resume_id")
        if not resume_id:
            raise HTTPException(status_code=400, detail="resume_id required")
        
        # DEBUG LOGGING
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Received view log request for resume {resume_id}. Data: {view_data}")

        result = await supabase_service.log_resume_view(resume_id, view_data)
        logger.info(f"Log view result for {resume_id}: {result}")
        
        if isinstance(result, str): # It's an UUID
            return {"success": True, "view_id": result}
        elif result is True: # Fallback if ID not returned
             logger.warning(f"Log view succeeded but no ID returned for {resume_id}")
             return {"success": True, "view_id": None}
        else:
             logger.error(f"Failed to log view for {resume_id}, result was False/None")
             raise HTTPException(status_code=500, detail="Failed to log view")
             
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/view/{view_id}/heartbeat")
async def view_heartbeat(view_id: str, data: Dict[str, Any] = Body(...)):
    """Update duration for a view session"""
    try:
        duration = data.get("duration_seconds")
        if duration is None:
             raise HTTPException(status_code=400, detail="duration_seconds required")
             
        success = await supabase_service.update_view_duration(view_id, duration)
        return {"success": success}
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))

from utils.auth_deps import get_current_user_id

@router.get("/dashboard")
async def get_dashboard_stats(user_id: str = Depends(get_current_user_id)):
    """Get aggregated analytics for user dashboard"""
    try:
        stats = await analytics_service.get_dashboard_analytics(user_id)
        return {"success": True, "data": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/download")
async def log_download(request: Request, download_data: Dict[str, Any] = Body(...)):
    """Log a download"""
    try:
        resume_id = download_data.get("resume_id")
        if not resume_id:
            raise HTTPException(status_code=400, detail="resume_id required")
            
        # Inject visitor IP if not present or invalid
        # Frontend sends "Triggered from Client" which crashes INET columns
        if not download_data.get("visitor_ip") or "Triggered" in str(download_data.get("visitor_ip")):
            if request.client:
                download_data["visitor_ip"] = request.client.host
            else:
                 download_data["visitor_ip"] = "127.0.0.1" # Fallback

        # Sanitize payload: Empty strings crash UUID columns in Postgres
        for key, value in download_data.items():
            if value == "":
                download_data[key] = None

        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Received download log request for {resume_id} with sanitized data: {download_data}")

        result = await supabase_service.log_resume_download(resume_id, download_data)
        if result:
            return {"success": True}
        else:
             # Just return success False instead of 500 to prevent frontend noise
             # or log error but keep endpoint alive
             logger.error("Failed to log download in Supabase")
             return {"success": False}
    except Exception as e:
        # Log error but don't crash frontend flow
        import logging
        logging.getLogger(__name__).error(f"Download logging error: {str(e)}")
        return {"success": False, "error": str(e)}

@router.get("/debug-dump")
async def debug_analytics_dump():
    """Temporary debug endpoint to inspect raw DB data"""
    try:

        # Fetch last 50 views
        views = await supabase_service.client.table("resume_views")\
            .select("*")\
            .order("viewed_at", desc=True)\
            .limit(50)\
            .execute()
            
        # Fetch last 50 downloads
        downloads = await supabase_service.client.table("resume_downloads")\
            .select("*")\
            .order("downloaded_at", desc=True)\
            .limit(50)\
            .execute()
            
        return {
            "debug_info": "Raw Data Dump",
            "view_count": len(views.data),
            "download_count": len(downloads.data),
            "sample_views": views.data,
            "sample_downloads": downloads.data
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/debug-force-update/{view_id}")
async def debug_force_update(view_id: str):
    """Force update a view duration to test RLS/Code"""
    try:
        success = await supabase_service.update_view_duration(view_id, 999)
        return {"success": success, "message": f"Attempted update for {view_id} to 999s"}
    except Exception as e:
        return {"success": False, "error": str(e)}
