from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, EmailStr

class ContactInfo(BaseModel):
    email: EmailStr
    phone: str = Field(..., pattern=r'^\+?[0-9][0-9\s-]{1,20}$')
    street_address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    links: Optional[List[Dict[str, str]]] = []

class Experience(BaseModel):
    title: str
    company: str
    city: Optional[str] = None
    country: Optional[str] = None
    location: Optional[str] = None # Legacy field
    start_date: str
    end_date: Optional[str] = None
    description: Union[str, List[str]]

class Project(BaseModel):
    title: str
    role: Optional[str] = None
    link: Optional[str] = None
    description: Optional[str] = None
    technologies: List[str] = []
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class Education(BaseModel):
    degree: str
    institution: str
    city: Optional[str] = None
    country: Optional[str] = None
    graduation_date: Optional[str] = None
    gpa: Optional[float] = None

class UserData(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    headline: Optional[str] = None
    date_of_birth: Optional[str] = None
    nationality: Optional[str] = None
    contact: ContactInfo
    summary: Optional[str] = None
    experience: List[Experience] = []
    projects: List[Project] = []
    education: List[Education] = []
    skills: List[str] = []
    certifications: Optional[List[str]] = []
    links: Optional[List[Dict[str, str]]] = []
    languages: Optional[List[Union[str, Dict[str, str]]]] = ["English"]

class CreateResumeRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=100)
    user_data: UserData
    template_style: Optional[str] = "professional"
    country: str = "Germany"
    language: str = "English"
    job_description: Optional[str] = None
    ignore_compliance: Optional[bool] = False

class UpdateResumeRequest(BaseModel):
    title: Optional[str] = None
    resume_data: Optional[Dict[str, Any]] = None
    template_style: Optional[str] = None
    job_description: Optional[str] = None
    regenerate_pdf: Optional[bool] = True

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[Any] = None
    progress: Optional[int] = 0
    step: Optional[str] = None
    error: Optional[str] = None

class RefineRequest(BaseModel):
    resumeId: str
    selectedText: str
    userInstruction: str
    currentContext: str = ""
    sectionId: Optional[str] = None

class APIResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
