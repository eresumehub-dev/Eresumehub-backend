import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from services.supabase_service import supabase_service
from services.profile_service import ProfileService
from services.ai_service import ai_service
from services.rag_service import RAGService
from services.resume_autocorrect import resume_autocorrect
from utils.resume_validator import ResumeComplianceValidator
from utils.pdf_utils import html_to_pdf
from utils.html_generator import HTMLGenerator

logger = logging.getLogger(__name__)

class ResumePipeline:
    """
    Orchestrates the entire resume generation lifecycle:
    Validation -> Enrichment -> AI Tailoring -> PDF Rendering -> DB Persistence.
    """

    def __init__(self, request_id: str, profile_service: ProfileService):
        self.request_id = request_id
        self.profile_service = profile_service
        self.logger = logger # Could be a child logger with request_id

    async def run(self, user: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the pipeline for a new resume request.
        """
        user_id = user["platform_user_id"]
        auth_user_id = user.get("auth_user_id")

        # 1. Enrichment & Data Preparation
        self.logger.info(f"[{self.request_id}] Starting ResumePipeline for user {user_id}")
        db_profile = await self.profile_service.get_profile(auth_user_id or user_id)
        
        # Merge logic to protect shared state
        user_data = {**(db_profile or {}), **data.get("user_data", {})}
        
        # Structure contact info correctly
        if "contact" not in user_data:
            user_data["contact"] = {
                "email": user_data.get("email", ""),
                "phone": user_data.get("phone", ""),
                "linkedin": user_data.get("linkedin_url", ""),
                "city": user_data.get("city", "")
            }
        
        # Merge nested contact overrides safely
        request_contact = data.get("user_data", {}).get("contact", {})
        if request_contact:
            user_data["contact"].update(request_contact)

        # 2. Compliance Validation
        country = data.get("country", user_data.get("country", "Germany"))
        if not data.get("skip_compliance", False):
            validation = ResumeComplianceValidator.validate(user_data, country)
            if not validation["valid"]:
                self.logger.warning(f"[{self.request_id}] Compliance validation failed")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "COMPLIANCE_ERROR",
                        "errors": validation["errors"],
                        "summary": "Mandatory fields missing for target market."
                    }
                )

        # 3. AI Orchestration
        job_description = data.get("job_description", "")
        # Determine Smart Title
        job_title = data.get("job_title") or data.get("user_data", {}).get("job_title", "Untitled Resume")
        if not job_title or job_title == "Untitled Resume":
            job_title = await ai_service.generate_resume_title(user_data, job_description)

        # Content Generation (Step A + Step B)
        generation_result = await ai_service.generate_tailored_resume(
            user_data=user_data,
            job_description=job_description,
            country=country,
            language=data.get("language", "English"),
            job_title=job_title
        )

        if not generation_result.get("success"):
            self.logger.error(f"[{self.request_id}] AI Generation Failed: {generation_result.get('error')}")
            raise HTTPException(status_code=500, detail=f"Resume generation failed: {generation_result.get('error')}")

        # 4. Post-Processing & Normalization
        resume_content = generation_result["resume_content"]
        spun_data = generation_result.get("spun_data", {})
        clean_summary = generation_result.get("generated_summary", "")

        enriched_data = {
            **user_data,
            "summary_text": resume_content,
            "professional_summary": clean_summary,
            "work_experiences": spun_data.get("work_experiences") or user_data.get("work_experiences", []),
            "skills": spun_data.get("skills") or user_data.get("skills", []),
            "educations": spun_data.get("educations") or user_data.get("educations", []),
            "headline": spun_data.get("headline", ""),
            "score": 0 # Placeholder for ATS
        }

        # Apply Reliability Layer (Auto-Correction)
        enriched_data = resume_autocorrect.autocorrect_for_country(enriched_data, country)

        # 5. PDF Rendering (xhtml2pdf in threadpool)
        rag_data = RAGService.get_complete_rag(country, data.get("language", "English"))
        html_content = HTMLGenerator.generate_html(
            text=resume_content,
            full_name=enriched_data.get("full_name", "Candidate"),
            contact_info=enriched_data.get("contact", {}),
            user_data=enriched_data,
            rag_data=rag_data,
            template_style=data.get("template_style", "professional")
        )

        pdf_bytes = await run_in_threadpool(html_to_pdf, html_content)

        # 6. Database Persistence
        resume_payload = {
            "title": job_title,
            "resume_data": enriched_data,
            "country": country,
            "language": data.get("language", "English"),
            "template_style": data.get("template_style", "professional"),
            "slug": data.get("slug") or f"resume-{uuid.uuid4().hex[:8]}",
            "job_description": job_description
        }

        # Create row
        resume = await supabase_service.create_resume(user_id, resume_payload)
        resume_id = resume["id"]

        # Upload & Upload
        filename = f"{resume.get('slug')}.pdf"
        pdf_url = await supabase_service.upload_resume_pdf(user_id, resume_id, pdf_bytes, filename)

        # Final Update with URL and ATS Score (Step 5)
        analysis = await ai_service.analyze_resume(resume_content, job_title, country, job_description)
        enriched_data["score"] = analysis.get("score", 0)

        update_payload = {
            "resume_data": enriched_data,
            "pdf_url": f"/api/v1/resume/{resume_id}/pdf",
            "pdf_file_size": len(pdf_bytes)
        }
        
        final_resume = await supabase_service.update_resume(resume_id, update_payload)

        # 7. Audit Logging
        await supabase_service.create_audit_log(
            user_id=user_id,
            action="RESUME_PIPELINE_COMPLETE",
            entity_type="resume",
            entity_id=resume_id,
            new_data={"request_id": self.request_id}
        )

        return final_resume or resume
