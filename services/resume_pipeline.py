import logging
import uuid
from datetime import datetime, timezone
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
from app_settings import Config

class PipelineError(Exception):
    """Base class for all resume pipeline failures (Staff+ Standard)."""
    def __init__(self, code: str, message: str, status_hint: int = 500):
        self.code = code
        self.message = message
        self.status_hint = status_hint
        super().__init__(message)

class ComplianceError(PipelineError): pass
class GenerationError(PipelineError): pass
class StorageError(PipelineError): pass
class AuthorizationError(PipelineError): pass

logger = logging.getLogger(__name__)

class ResumePipeline:
    """
    Orchestrates the entire resume generation lifecycle:
    Validation -> Enrichment -> AI Tailoring -> PDF Rendering -> DB Persistence.
    """

    def __init__(
        self, 
        request_id: str, 
        profile_service: ProfileService,
        ai_service: Any,
        supabase_service: Any,
        analytics_service: Any,
        rq_job: Any = None
    ):
        self.request_id = request_id
        self.profile_service = profile_service
        self.ai_service = ai_service
        self.supabase_service = supabase_service
        self.analytics_service = analytics_service
        self.rq_job = rq_job
        self.logger = logger

    async def _update_status(self, step: str, progress: int):
        """Update the progress of the current job if available."""
        if self.rq_job:
            try:
                self.rq_job.meta['step'] = step
                self.rq_job.meta['progress'] = progress
                # Staff+ Safety: Offload synchronous Redis call
                await run_in_threadpool(self.rq_job.save_meta)
                logger.info(f"[{self.request_id}] Progress: {step} ({progress}%)")
            except Exception as e:
                logger.warning(f"[{self.request_id}] Failed to update job meta: {e}")

    @classmethod
    async def run_for_user(cls, request_id: str, profile_service: ProfileService, ai_service: Any, supabase_service: Any, analytics_service: Any, user: Dict[str, Any], data: Dict[str, Any], rq_job: Any = None):
        """Staff+ Entry Point with Total Fault-Tolerance."""
        instance = cls(request_id, profile_service, ai_service, supabase_service, analytics_service, rq_job)
        try:
            action = data.get("action", "create")
            if action == "create":
                return await instance._run_create_flow(user, data)
            elif action == "improve":
                return await instance._run_improve_flow(user, data)
            elif action == "enhance":
                return await instance._run_enhance_flow(user, data)
            else:
                raise PipelineError(code="INVALID_ACTION", message=f"Pipeline does not support action: {action}")
        except Exception as e:
            logger.error(f"[{request_id}] PIPELINE FATAL ERROR: {str(e)}")
            # Ensure we record failure in audit even on hard crash
            try:
                await instance.supabase_service.create_audit_log(
                    user_id=user.get("auth_user_id"),
                    action=f"PIPELINE_{action.upper()}_FAILED",
                    entity_type="pipeline",
                    new_data={"error": str(e), "request_id": request_id}
                )
            except:
                pass
            raise

    # -----------------------------
    # 1. Atomic Pipeline Steps (Elite Isolation)
    # -----------------------------

    async def _step_prepare_data(self, user: Dict[str, Any], data: Dict[str, Any]):
        await self._update_status("Fetching Profile Data", 10)
        auth_user_id = user["auth_user_id"]
        
        db_profile = await self.profile_service.get_profile(auth_user_id)
        user_data = {**(db_profile or {}), **data.get("user_data", {})}
        
        if "contact" not in user_data:
            user_data["contact"] = {
                "email": user_data.get("email", ""),
                "phone": user_data.get("phone", ""),
                "linkedin": user_data.get("linkedin_url", ""),
                "city": user_data.get("city", "")
            }
        
        request_contact = data.get("user_data", {}).get("contact", {})
        if request_contact:
            user_data["contact"].update(request_contact)
            
        return user_data

    async def _step_validate(self, user_data: Dict[str, Any], data: Dict[str, Any]):
        await self._update_status("Market Compliance Check", 20)
        country = data.get("country", user_data.get("country", "Germany"))
        if not data.get("skip_compliance", False):
            from utils.resume_validator import ResumeComplianceValidator
            validation = ResumeComplianceValidator.validate(user_data, country)
            if not validation["valid"]:
                logger.warning(f"[{self.request_id}] Compliance validation failed")
                raise ComplianceError(code="COMPLIANCE_ERROR", message="Mandatory fields missing.", status_hint=400)
        return country

    async def _step_generate_content(self, user_data: Dict[str, Any], data: Dict[str, Any], country: str):
        await self._update_status("AI Content Tailoring", 30)
        job_description = data.get("job_description", "")
        job_title = data.get("job_title") or data.get("user_data", {}).get("job_title", "Untitled Resume")
        
        import asyncio
        if not job_title or job_title == "Untitled Resume":
            self.logger.info(f"[{self.request_id}] Title missing, invoking AI titler...")
            job_title = await asyncio.wait_for(
                self.ai_service.generate_resume_title(user_data, job_description, request_id=self.request_id),
                timeout=20.0
            )

        await self._update_status("Generating Smart Sections", 50)
        generation_result = await asyncio.wait_for(
            self.ai_service.generate_tailored_resume(
                user_data=user_data,
                job_description=job_description,
                country=country,
                language=data.get("language", "English"),
                job_title=job_title,
                request_id=self.request_id
            ),
            timeout=Config.AI_REQUEST_TIMEOUT 
        )

        if not generation_result.get("success"):
            error_code = generation_result.get("error", "AI_FAIL")
            user_msg = "AI generation failed"
            if error_code == "PROVIDER_FAIL":
                user_msg = "AI services are currently busy. Please try again in a few minutes."
            elif error_code == "PARSE_ERROR":
                user_msg = "AI response was malformed. Retrying usually helps."
                
            raise GenerationError(code=error_code, message=user_msg)
            
        return generation_result, job_title

    async def _step_post_process(self, user_data: Dict[str, Any], generation_result: Dict[str, Any], country: str):
        await self._update_status("Applying Market Rules", 70)
        resume_content = generation_result["resume_content"]
        spun_data = generation_result.get("spun_data", {})
        
        enriched_data = {
            **user_data,
            "summary_text": resume_content,
            "professional_summary": generation_result.get("generated_summary", ""),
            "work_experiences": spun_data.get("work_experiences") or user_data.get("work_experiences", []),
            "skills": spun_data.get("skills") or user_data.get("skills", []),
            "educations": spun_data.get("educations") or user_data.get("educations", []),
            "headline": spun_data.get("headline", ""),
            "score": 0 
        }
        return resume_autocorrect.autocorrect_for_country(enriched_data, country), resume_content

    async def _step_render_and_analyze(self, resume_content: str, enriched_data: Dict[str, Any], job_title: str, country: str, data: Dict[str, Any]):
        await self._update_status("High-Fidelity PDF & ATS Analysis", 85)
        import asyncio
        rag_data = RAGService.get_complete_rag(country, data.get("language", "English"))
        html_content = HTMLGenerator.generate_html(
            text=resume_content,
            full_name=enriched_data.get("full_name", "Candidate"),
            contact_info=enriched_data.get("contact", {}),
            user_data=enriched_data,
            rag_data=rag_data,
            template_style=data.get("template_style", "professional")
        )

        pdf_task = run_in_threadpool(html_to_pdf, html_content)
        analysis_task = self.ai_service.analyze_resume(resume_content, job_title, country, data.get("job_description", ""))
        
        pdf_bytes, analysis = await asyncio.gather(pdf_task, analysis_task)
        enriched_data["score"] = analysis.get("score", 0)
        return pdf_bytes, enriched_data

    async def _step_persist(self, user_id: str, job_title: str, enriched_data: Dict[str, Any], country: str, data: Dict[str, Any], pdf_bytes: bytes):
        """Staff+ Elite: Idempotent Persistence Layer (Find-or-Update)."""
        await self._update_status("Saving to Cloud Storage", 95)
        
        # 1. Internal Idempotency Check: Look for existing resume with this request_id
        # We look inside the resume_data column since 'metadata' column is not in production schema.
        try:
            self.logger.info(f"[{self.request_id}] Persist: Checking for existing record (Idem-Key: {self.request_id})")
            existing = await self.supabase_service.client.table("resumes")\
                .select("*")\
                .eq("user_id", user_id)\
                .eq("resume_data->metadata->>request_id", self.request_id)\
                .execute()
            
            if existing.data:
                resume_id = existing.data[0]["id"]
                slug = existing.data[0]["slug"]
                logger.info(f"[{self.request_id}] Pipeline Idem-Match: Reusing existing resume {resume_id}")
                
                # Update existing
                pdf_url = await self.supabase_service.upload_resume_pdf(user_id, resume_id, pdf_bytes, f"{slug}.pdf")
                final_resume = await self.supabase_service.update_resume(resume_id, {
                    "resume_data": {**enriched_data, "metadata": {"request_id": self.request_id}},
                    "pdf_url": pdf_url,
                    "pdf_file_size": len(pdf_bytes)
                })
                return resume_id, final_resume
        except Exception as e:
            logger.warning(f"[{self.request_id}] Idempotency check failed: {e}")

        # 2. Proceed with Create if no match
        slug = f"resume-{uuid.uuid4().hex[:8]}"
        # Ensure metadata is nested within resume_data to comply with schema
        enriched_data["metadata"] = {"request_id": self.request_id}
        
        resume_payload = {
            "title": job_title,
            "resume_data": enriched_data,
            "country": country,
            "language": data.get("language", "English"),
            "template_style": data.get("template_style", "professional"),
            "slug": slug,
            "job_description": data.get("job_description", "")
        }

        resume_id = None
        try:
            self.logger.info(f"[{self.request_id}] Persist: Inserting into DB (User: {user_id})")
            resume = await self.supabase_service.create_resume(user_id, resume_payload)
            resume_id = resume["id"]
            self.logger.info(f"[{self.request_id}] Persist: Resume ID {resume_id} created. Uploading PDF...")
            
            pdf_url = await self.supabase_service.upload_resume_pdf(user_id, resume_id, pdf_bytes, f"{slug}.pdf")
            
            self.logger.info(f"[{self.request_id}] Persist: PDF Uploaded. Updating record with URL...")
            final_resume = await self.supabase_service.update_resume(resume_id, {
                "resume_data": enriched_data,
                "pdf_url": pdf_url, 
                "pdf_file_size": len(pdf_bytes)
            })
            self.logger.info(f"[{self.request_id}] Persist: SUCCESS for {resume_id}")
            return resume_id, final_resume
        except Exception as e:
            if resume_id:
                await self.supabase_service.delete_resume(resume_id)
            logger.error(f"[{self.request_id}] Storage failure: {e}")
            raise StorageError(code="STORAGE_FAIL", message="Resume could not be saved.")

    # -----------------------------
    # 2. Main High-Level Flows
    # -----------------------------

    async def _run_create_flow(self, user: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        auth_user_id = user["auth_user_id"]
        start_time = datetime.now(timezone.utc)
        
        try:
            self.logger.info(f"[{self.request_id}] Pipeline: Starting CREATE flow for {auth_user_id}")
            # 0. Pre-flight DB Check
            await self.supabase_service.client.table("users").select("id").limit(1).execute()
            
            # 1. Step-Isolated Execution
            user_data = await self._step_prepare_data(user, data)
            country = await self._step_validate(user_data, data)
            gen_res, job_title = await self._step_generate_content(user_data, data, country)
            enriched_data, resume_content = await self._step_post_process(user_data, gen_res, country)
            pdf_bytes, enriched_data = await self._step_render_and_analyze(resume_content, enriched_data, job_title, country, data)
            resume_id, final_resume = await self._step_persist(auth_user_id, job_title, enriched_data, country, data, pdf_bytes)

            # 2. Audit & Metrics (Success)
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            await self._record_metrics("success", duration)
            
            await self.supabase_service.create_audit_log(user_id=auth_user_id, action="RESUME_PIPELINE_COMPLETE", 
                                                        entity_type="resume", entity_id=resume_id, 
                                                        new_data={"request_id": self.request_id, "score": enriched_data["score"]})
            
            return {"success": True, "resume_id": resume_id, "data": final_resume}
            
        except Exception as e:
            await self._record_metrics("failed")
            # Clear idempotency early if we hit a transient error? 
            # Implementation choice: let the 5-min TTL handle it to protect AI costs.
            raise

    async def _record_metrics(self, status: str, duration: float = 0):
        """Staff+ Performance Monitoring Layer."""
        if self.rq_job and hasattr(self.rq_job, "connection"):
            try:
                pipe = self.rq_job.connection.pipeline()
                pipe.incr("metrics:jobs:total")
                pipe.incr(f"metrics:jobs:{status}")
                if status == "success" and duration > 0:
                    # Rolling average store (lpush + ltrim for last 100)
                    pipe.lpush("metrics:jobs:latency", duration)
                    pipe.ltrim("metrics:jobs:latency", 0, 99)
                await run_in_threadpool(pipe.execute)
            except Exception as e:
                logger.warning(f"Metrics record failed: {e}")
    async def _run_improve_flow(self, user: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Flow for improving an existing resume text with progress tracking.
        """
        auth_user_id = user["auth_user_id"]
        text = data.get("resume_text", "")
        
        # Staff+ Observability: Warning for truncation (v3.13.0)
        if len(text) > 5000:
            logger.warning(f"[{self.request_id}] Resume text truncated from {len(text)} to 5000 chars for improve flow")
            
        country = data.get("country", "Germany")
        job_description = data.get("job_description", "")

        self.logger.info(f"[{self.request_id}] Running Improve Flow for user {auth_user_id}")
        await self._update_status("AI Deep Analysis", 40)

        # 1. AI Enrichment
        improvement_prompt = f"""
        Improve this resume for a {country} job application.
        Job Description: {job_description}
        Original Resume Text: {text[:5000]}
        
        Instructions:
        1. Fix all ATS compatibility issues.
        2. Apply {country}-specific formatting.
        3. Optimize keywords specifically for the job description.
        4. Return ONLY the improved resume text in a clear professional layout.
        """
        
        import asyncio
        improved_text = await asyncio.wait_for(
            self.ai_service.call_api(improvement_prompt, temperature=0.3, request_id=self.request_id),
            timeout=45.0
        )
        
        if not improved_text:
            await self._update_status("AI Improvement Failed", 0)
            raise GenerationError(code="IMPROVE_FAIL", message="AI Improvement failed")

        await self._update_status("Finalizing Result", 95)
        
        # 🛡️ Persist Result (Identified in review)
        # Create a new resume record for the improved text
        improve_payload = {
            "title": f"Improved - {data.get('country', 'International')}",
            "resume_data": {"summary_text": improved_text, "full_name": user.get("full_name", "User")},
            "country": country,
            "job_description": job_description,
            "status": "ready"
        }
        resume = await self.supabase_service.create_resume(auth_user_id, improve_payload)

        # 2. Return result
        return {
            "success": True,
            "resume_id": resume["id"],
            "improved_text": improved_text,
            "original_text": text[:500],
            "country": country
        }

    async def _run_enhance_flow(self, user: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the pipeline for an existing resume enhancement with progress tracking.
        """
        auth_user_id = user["auth_user_id"]
        resume_id = data.get("resume_id")
        
        # 1. Fetch Existing State
        await self._update_status("Retrieving Resume", 10)
        self.logger.info(f"[{self.request_id}] Starting Enhancement Flow for resume {resume_id}")
        resume = await self.supabase_service.get_resume(resume_id)

        if not resume:
            raise PipelineError(code="NOT_FOUND", message="Resume not found", status_hint=404)
        
        owner_id = resume.get("user_id")
        if owner_id != auth_user_id:
             raise AuthorizationError(code="FORBIDDEN", message="Not authorized to enhance this resume", status_hint=403)

        current_data = resume.get("resume_data", {})
        job_description = resume.get("job_description", "")
        country = resume.get("country", "Germany")
        language = resume.get("language", "English")
        title = resume.get("title", current_data.get("job_title", "Professional"))

        # 2. AI Orchestration
        await self._update_status("AI Re-Tailoring", 40)
        generation_result = await self.ai_service.generate_tailored_resume(
            user_data=current_data,
            job_description=job_description,
            country=country,
            language=language,
            job_title=title,
            request_id=self.request_id
        )

        if not generation_result.get("success"):
            await self._update_status("Enhancement Failed", 0)
            raise GenerationError(code="ENHANCE_FAIL", message=f"Enhancement failed: {generation_result.get('error')}")

        # 3. Render Polished PDF
        await self._update_status("Polished PDF Rendering", 80)
        resume_content = generation_result["resume_content"]
        rag_data = RAGService.get_complete_rag(country, language)
        html_content = HTMLGenerator.generate_html(
            text=resume_content,
            full_name=current_data.get("full_name", "Candidate"),
            contact_info=current_data.get("contact", {}),
            user_data=current_data,
            rag_data=rag_data,
            template_style=resume.get("template_style", "professional")
        )

        pdf_bytes = await run_in_threadpool(html_to_pdf, html_content)

        # 4. Save & Finalize
        await self._update_status("Cloud Sync", 95)
        update_payload = {
            "resume_data": {**current_data, "summary_text": resume_content},
            "pdf_url": await self.supabase_service.upload_resume_pdf(auth_user_id, resume_id, pdf_bytes, f"{resume.get('slug')}.pdf"),
            "pdf_file_size": len(pdf_bytes)
        }
        
        updated = await self.supabase_service.update_resume(resume_id, update_payload)
        
        return {"success": True, "data": updated}

async def run_pipeline_job(request_id: str, user: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """
    RQ-compatible worker task.
    Wraps the async ResumePipeline execution.
    """
    import asyncio
    from rq import get_current_job
    from services.analytics_service import analytics_service
    
    # detect current job context for progress tracking
    job = get_current_job()
    
    # Staff+ Optimization: Manage loop manually to prevent 'loop already running' in RQ
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(ResumePipeline.run_for_user(
            request_id=request_id,
            profile_service=ProfileService(supabase_service),
            ai_service=ai_service,
            supabase_service=supabase_service,
            analytics_service=analytics_service,
            user=user,
            data=data,
            rq_job=job
        ))
    except Exception:
        # Bare raise to preserve original traceback (Identified in review)
        raise
    finally:
        # Staff+ Security: Ensure high-entropy locks are guaranteed to be cleared (v3.12.0)
        try:
            # Re-fetch keys from metadata to ensure we delete the exact ones we created
            idempotency_key = job.meta.get("idempotency_key")
            auth_user_id = user.get("auth_user_id")
            debounce_key = f"debounce:{data.get('action', 'create')}:{auth_user_id}"
            
            # Use synchronous execution via job connection (v3.13.0)
            cleanup_pipe = job.connection.pipeline()
            if idempotency_key:
                cleanup_pipe.delete(idempotency_key)
            cleanup_pipe.delete(debounce_key)
            cleanup_pipe.execute() 
            logger.info(f"[{request_id}] Pipeline cleanup complete (locks released)")
        except Exception as e:
            logger.warning(f"[{request_id}] Cleanup failed: {e}")
        
        loop.close()
