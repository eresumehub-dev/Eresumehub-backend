from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

class EventContext(BaseModel):
    ip: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    device_type: Optional[str] = None # mobile | desktop | tablet
    browser: Optional[str] = None
    os: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    referrer: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None

class StandardEvent(BaseModel):
    event_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None # Logged in user (viewer)
    session_id: str
    context: Optional[EventContext] = None
    properties: Dict[str, Any] = {}

    @validator('event_name')
    def validate_event_name(cls, v):
        allowed = [
            "resume_view_started",
            "resume_view_heartbeat",
            "resume_view_ended",
            "resume_download",
            "section_view",
            "return_visit",
            "engagement_classified"
        ]
        if v not in allowed:
            # We'll allow custom events but log a warning in the service
            pass
        return v
