import logging
import re
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
        
        # Hybrid Compliance UX (v16.4.18 Hardening)
        # We no longer hard-block on compliance fields (DOB, Nationality, etc.)
        # Instead, we identify gaps and inject them as warnings for the AI to adapt.
        from utils.resume_validator import ResumeComplianceValidator
        validation = ResumeComplianceValidator.validate(user_data, country)
        
        if not validation["valid"]:
            # Capture strictly the MISSING metadata fields
            missing_fields = [err.get("field", "unknown") for err in validation.get("errors", [])]
            self.compliance_gap = missing_fields
            self.logger.warning(f"[{self.request_id}] ⚠️ Compliance gap detected for {country}: {missing_fields}. Continuing with AI adaptation.")
            
        return country

    async def _step_generate_content(self, user_data: Dict[str, Any], data: Dict[str, Any], country: str):
        await self._update_status("AI Content Tailoring", 30)
        job_description = data.get("job_description", "")
        
        # Parse job title from 'title', 'job_title', or user's headline
        job_title = data.get("title") or data.get("job_title") or data.get("user_data", {}).get("job_title") or data.get("user_data", {}).get("headline")
        
        if not job_title or job_title.strip() == "" or job_title == "Untitled Resume":
            raise GenerationError(code="VALIDATION_ERROR", message="Job title is required")
            
        import asyncio
        from services.rag_service import RAGService
        
        # Phase 1: Load RAG BEFORE calling AI
        rag_data = RAGService.get_complete_rag(country, data.get("language", "English"))

        await self._update_status("Generating Smart Sections", 50)
        generation_result = await asyncio.wait_for(
            self.ai_service.generate_tailored_resume(
                user_data=user_data,
                job_description=job_description,
                country=country,
                language=data.get("language", "English"),
                job_title=job_title,
                rag_data=rag_data,
                compliance_gap=self.compliance_gap, # 🧬 Phase 3.1: Pass gaps for adaptation
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
            
        return generation_result, job_title, rag_data

    def _audit_compliance(self, resume_content: Dict[str, Any], country: str) -> List[str]:
        """Internal Helper: Programmatic audit of the generated content."""
        violations = []
        c_lower = country.lower()
        
        # 1. Metrics Check (Strict Regex with word boundaries)
        # Allows for: 5%, 50+, $500, 5,000, 5.5, five (numeric or symbol)
        metric_pattern = re.compile(r'\d+|%|\$|million|billion|thousand', re.IGNORECASE)
        experience = resume_content.get("experience", resume_content.get("work_experiences", []))
        
        for exp in experience:
            bullets = exp.get("achievements", [])
            if isinstance(bullets, str): bullets = [bullets]
            for bullet in bullets:
                if not metric_pattern.search(str(bullet)):
                    violations.append(f"Metric missing in experience: {exp.get('job_title')} - '{bullet[:40]}...'")
        
        # 2. Weak Verbs Check (Regex with word boundaries for precision)
        weak_verbs = ["helped", "contributing", "assisted", "participated", "involved"]
        verb_pattern = re.compile(r'\b(' + '|'.join(weak_verbs) + r')\b', re.IGNORECASE)
        
        for exp in experience:
            bullets = exp.get("achievements", [])
            if isinstance(bullets, str): bullets = [bullets]
            for bullet in bullets:
                if verb_pattern.search(str(bullet)):
                    # Extract the matching verb for clarity (v16.4.17)
                    match = verb_pattern.search(str(bullet))
                    violations.append(f"Weak verb '{match.group(0)}' found in: '{bullet[:40]}...'")

        # 3. Mandatory Sections (Germany) - Normalized casing check
        if c_lower == "germany":
            if not resume_content.get("languages") or len(resume_content.get("languages", [])) == 0:
                violations.append("Mandatory section 'Languages' missing for Germany.")
        
        return violations

    async def _step_validate_generated_output(self, gen_res: Dict[str, Any], country: str, rag_data: Dict[str, Any]):
        """Staff+ Compliance Enforcement: Two-pass Audit & Correction Loop."""
        await self._update_status("Market Compliance Audit", 60)
        
        # DEBUG: Pipeline Validation Phase Started
        print(f"[{self.request_id}] 🔍 PIPELINE: Running validation step for {country}")
        
        current_pass = 1
        max_passes = 2
        
        while current_pass <= max_passes:
            resume_content = gen_res.get("resume_content", {})
            violations = self._audit_compliance(resume_content, country)
            
            # DEBUG: Log results per pass
            print(f"[{self.request_id}] 🔍 PASS {current_pass} | Violations found: {len(violations)}")
            if violations:
                for v in violations: print(f"   [!] {v}")
            
            # Education Cleanup (Done every pass to ensure structural integrity)
            education = resume_content.get("education", resume_content.get("educations", []))
            original_edu_count = len(education)
            resume_content["education"] = [
                edu for edu in education 
                if "pre university" not in str(edu.get("degree", "")).lower() 
                and "junior college" not in str(edu.get("degree", "")).lower()
                and "high school" not in str(edu.get("degree", "")).lower()
            ]
            
            if not violations:
                print(f"[{self.request_id}] ✅ Compliance: Pass {current_pass} cleared (0 violations).")
                break
            
            if current_pass == max_passes:
                print(f"[{self.request_id}] ⚠️ Hard Fail-Safe: Compliance failed after {max_passes} passes.")
                break
                
            # DEBUG: Correction Pass Started
            print(f"[{self.request_id}] 🛠️ Triggering correction pass for {len(violations)} issues...")
            await self._update_status(f"Failing Pass {current_pass}: Correcting Content", 65)
            
            # Correction Pass: Full JSON Regeneration
            corrected_content = await self.ai_service.enforce_compliance_correction(
                json_payload=resume_content,
                violations=violations,
                country=country,
                request_id=self.request_id
            )
            
            # Update gen_res state (Strict override)
            gen_res["resume_content"] = corrected_content
            if "generated_summary" in corrected_content:
                gen_res["generated_summary"] = corrected_content["generated_summary"]
            
            current_pass += 1
            
        print(f"[{self.request_id}] 🏁 PIPELINE: Validation complete.")
        return gen_res

    async def _step_post_process(self, user_data: Dict[str, Any], generation_result: Dict[str, Any], country: str):
        await self._update_status("Applying Market Rules", 70)
        # ai_service.generate_tailored_resume returns `resume_content` as `{**user_data, **tailored}`
        generated_data = generation_result.get("resume_content", {})
        
        enriched_data = {
            **user_data,
            **generated_data, # Safely overlay all generated schema elements (experience, skills, projects)
            "professional_summary": generation_result.get("generated_summary", ""),
            "headline": generated_data.get("headline", user_data.get("headline", "")),
            "score": 0 
        }
        
        # Ensure we have the canonical keys for the templates
        if not enriched_data.get("experience"):
             enriched_data["experience"] = enriched_data.get("work_experiences", user_data.get("experience", []))
        if not enriched_data.get("education"):
             enriched_data["education"] = enriched_data.get("educations", user_data.get("education", []))
             
        return resume_autocorrect.autocorrect_for_country(enriched_data, country), generated_data

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

        # Staff+ Resilience: Ensure resume_content passed to analyzer is a string (v16.4.16)
        # The AI analyzer expects a narrative text for ATS scoring.
        analysis_content = resume_content
        if isinstance(resume_content, dict):
             # Format experience and skills into a readable string for AI context
             exp_str = "\n".join([f"{e.get('job_title')} at {e.get('company')}: {', '.join(e.get('achievements', []))}" for e in resume_content.get("experience", resume_content.get("work_experiences", []))])
             skills_str = ", ".join(resume_content.get("skills", []))
             analysis_content = f"SUMMARY: {resume_content.get('professional_summary')}\nEXPERIENCE:\n{exp_str}\nSKILLS: {skills_str}"

        pdf_task = run_in_threadpool(html_to_pdf, html_content)
        analysis_task = self.ai_service.analyze_resume(analysis_content, job_title, country, data.get("job_description", ""))
        
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
            gen_res, job_title, rag_data = await self._step_generate_content(user_data, data, country)
            
            # Post-Generation Compliance Audit
            gen_res = await self._step_validate_generated_output(gen_res, country, rag_data)
            
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
        generation_result, job_title, rag_data = await self._step_generate_content(
            user_data=current_data,
            data=data,
            country=country
        )

        # Post-Generation Compliance Audit
        generation_result = await self._step_validate_generated_output(generation_result, country, rag_data)

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

