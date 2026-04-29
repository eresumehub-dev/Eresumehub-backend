from fastapi import APIRouter, Depends, HTTPException, Body, Request, BackgroundTasks
from services.supabase_service import supabase_service
from services.analytics_service import AnalyticsService
from models.event_schema import StandardEvent
from typing import Dict, Any, Optional, List
import logging

import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])
@router.post("/events/track")
async def track_event(request: Request, event: StandardEvent):
    """
    Standard GA-Level Event Tracker (v12.0.0)
    Ensures every interaction follows the strict analytical contract.
    """
    try:
        # 1. Server-Side Enrichment
        if not event.context:
            from models.event_schema import EventContext
            event.context = EventContext()
            
        # Inject IP if missing
        if not event.context.ip:
            if request.client:
                event.context.ip = request.client.host
            else:
                event.context.ip = "127.0.0.1"

        # 2. Schema-Aware Persistence
        # We push to the 'events_raw' table in Supabase
        event_dict = event.dict()
        
        # Flatten context for easier SQL querying (or keep as JSONB)
        # For this design, we'll store context and properties as JSONB
        success = await supabase_service.client.table("events_raw").insert(event_dict).execute()
        
        if success:
            logger.info(f"Event Tracked: {event.event_name} | Session: {event.session_id}")
            return {"success": True, "event_id": event.event_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to persist event")
            
    except Exception as e:
        logger.error(f"Event Track Fail: {str(e)}")
        # Don't crash frontend trackers, just return fail
        return {"success": False, "error": "Failed to track event"}

@router.post("/view")
async def log_view(request: Request, background_tasks: BackgroundTasks, view_data: Dict[str, Any] = Body(...)):
    """Log a view start and return view_id (Legacy Wrapper -> V12 Tracker)"""
    try:
        # Legacy mapping (v12.0.0)
        resume_id = view_data.get("resume_id")
        if not resume_id:
            raise HTTPException(status_code=400, detail="resume_id required")
            
        # Forward to new tracking system internally
        from models.event_schema import StandardEvent, EventContext
        event = StandardEvent(
            event_name="resume_view_started",
            session_id=view_data.get("session_id") or "legacy_session",
            user_id=view_data.get("viewer_user_id"),
            context=EventContext(
                ip=request.client.host if request.client else "127.0.0.1",
                device_type=view_data.get("device_type"),
                browser=view_data.get("browser"),
                referrer=view_data.get("referrer")
            ),
            properties={"resume_id": resume_id}
        )
        
        # Also keep legacy DB write as safety until events_raw is fully verified
        result = await supabase_service.log_resume_view(resume_id, view_data)
        
        # Background track (v16.5.2)
        background_tasks.add_task(supabase_service.client.table("events_raw").insert(event.dict()).execute)
        
        if isinstance(result, str):
            return {"success": True, "view_id": result}
        else:
             return {"success": True, "view_id": None}
             
    except Exception as e:
        logger.error(f"View logging failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/view/{view_id}/heartbeat")
async def view_heartbeat(view_id: str, data: Dict[str, Any] = Body(...)):
    """Update duration and behavioral signals (Legacy -> V12 Wrapper)"""
    try:
        success = await supabase_service.update_view_duration(view_id, data)
        return {"success": success}
    except Exception as e:
         logger.error(f"Heartbeat failed: {e}")
         raise HTTPException(status_code=500, detail="Internal server error")

from utils.auth_deps import get_current_user_id

@router.get("/dashboard")
async def get_dashboard_stats(request: Request, user_id: str = Depends(get_current_user_id)):
    """Get aggregated analytics from the Intelligence Engine (V12)"""
    try:
        analytics_service = request.app.state.analytics_service
        stats = await analytics_service.get_dashboard_analytics(user_id)
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"Dashboard stats failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/download")
async def log_download(request: Request, background_tasks: BackgroundTasks, download_data: Dict[str, Any] = Body(...)):
    """Log a download (Legacy -> V12 Wrapper)"""
    try:
        resume_id = download_data.get("resume_id")
        if not resume_id:
            raise HTTPException(status_code=400, detail="resume_id required")
            
        # Forward to new tracking system internally
        from models.event_schema import StandardEvent
        event = StandardEvent(
            event_name="resume_download",
            session_id=download_data.get("session_id") or "legacy_dl",
            properties={"resume_id": resume_id, "file_format": "pdf"}
        )
        # Background track (v16.5.2)
        background_tasks.add_task(supabase_service.client.table("events_raw").insert(event.dict()).execute)

        result = await supabase_service.log_resume_download(resume_id, download_data)
        return {"success": result}
    except Exception as e:
        logger.error(f"Download logging failed: {e}")
        return {"success": False, "error": "Internal server error"}

@router.get("/nudges")
async def get_active_nudges(request: Request, user_id: str = Depends(get_current_user_id)):
    """Fetch prioritized, confidence-scored nudges (v14.0.0)"""
    try:
        analytics_service = request.app.state.analytics_service
        nudges = await analytics_service.get_active_nudges(user_id)
        return {"success": True, "data": nudges}
    except Exception as e:
        logger.error(f"Nudge Fetch Error: {e}")
        return {"success": False, "error": "Internal server error"}

@router.post("/nudges/dismiss")
async def dismiss_nudge(
    nudge_data: Dict[str, Any] = Body(...),
    user_id: str = Depends(get_current_user_id)
):
    """Mark a nudge as dismissed to prevent alert fatigue (v14.0.0)"""
    try:
        success = await supabase_service.update_user_nudge_state(
            user_id=user_id,
            nudge_type=nudge_data.get("nudge_type"),
            resume_id=nudge_data.get("resume_id"),
            status="dismissed",
            confidence=nudge_data.get("confidence", 0.0)
        )
        return {"success": success}
    except Exception as e:
        logger.error(f"Nudge Dismiss Error: {e}")
        return {"success": False, "error": "Internal server error"}
