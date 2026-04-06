"""
AI Service Layer (Refactored with Gemini API)
Handles all AI-powered operations using Google Gemini as primary, OpenRouter as fallback.
"""
import os
import json
import logging
import httpx
import base64
import asyncio
import re
import math
import urllib.parse
from typing import Dict, List, Any, Optional, Union
from dotenv import load_dotenv
import copy
from pydantic import BaseModel, Field, ValidationError
from datetime import datetime, timedelta
import difflib
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from configurations.countries import get_country_context, get_country_fallback_data
from app_settings import Config

load_dotenv()

logger = logging.getLogger(__name__)

# --- SCHEMA VALIDATION MODELS ---
class ATSAnalysisResponse(BaseModel):
    qualification_score: int = Field(..., ge=0, le=100)
    strengths: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    suggested_role: Optional[str] = None

class TailoredResumeResponse(BaseModel):
    professional_summary: str
    headline: str
    work_experiences: List[Dict[str, Any]]
    educations: List[Dict[str, Any]]
    projects: List[Dict[str, Any]] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    languages: List[Dict[str, Any]] = Field(default_factory=list)

class ExtractionResponse(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    headline: Optional[str] = None
    summary: Optional[str] = None
    work_experiences: List[Dict[str, Any]] = Field(default_factory=list)
    educations: List[Dict[str, Any]] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    languages: List[Dict[str, Any]] = Field(default_factory=list)
    projects: List[Dict[str, Any]] = Field(default_factory=list)
    certifications: List[Dict[str, Any]] = Field(default_factory=list)

# --- JOB SCOPE ARCHITECTURE CONFIG ---
DOMAIN_SIGNALS = {
    "AI / ML": ["ai", "ml", "learning", "neural", "llm", "generative", "agent", "rag", "automation", "nlp", "vision", "tensorflow", "pytorch"],
    "Backend": ["backend", "api", "database", "sql", "nosql", "server", "microservices", "distributed", "python", "java", "golang", "node", "infrastructure"],
    "Frontend": ["frontend", "ui", "ux", "react", "vue", "angular", "css", "html", "javascript", "typescript", "component", "styling"],
    "Full-Stack": ["fullstack", "full-stack", "end-to-end", "comprehensive", "cross-functional"],
    "DevOps": ["devops", "ci/cd", "pipeline", "docker", "kubernetes", "aws", "azure", "gcp", "terraform", "monitoring", "linux"],
    "Data": ["data", "analytics", "pipeline", "warehouse", "etl", "spark", "hadoop", "bi", "visualization", "statistics"],
    "Product": ["product", "roadmap", "strategy", "stakeholder", "requirement", "user story", "backlog", "agile", "scrum"],
    "Design": ["design", "figma", "sketch", "prototyping", "wireframe", "typography", "branding", "illustration"],
    "QA": ["qa", "testing", "automation", "selenium", "cypress", "unit test", "integration test", "quality", "bug"],
    "Security": ["security", "pentesting", "vulnerability", "encryption", "compliance", "firewall", "identity", "cyber"]
}

GLOBAL_FORBIDDEN_PHRASES = [
    "full-stack developer", "fullstack developer", 
    "end-to-end development", "across multiple domains", 
    "wide range of technologies", "various applications", 
    "hands-on experience across"
]

class AIService:
    def __init__(self):
        # Gemini API (primary - free and reliable)
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip('"').strip("'")
        self.gemini_url = "https://generativelanguage.googleapis.com/v1beta"
        
        # Groq API (High Performance)
        self.groq_api_key = Config.GROQ_API_KEY
        self.groq_url = "https://api.groq.com/openai/v1/chat/completions"

        # OpenRouter (fallback)
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip('"').strip("'")
        self.openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
        
        if not self.gemini_api_key and not self.openrouter_api_key and not self.groq_api_key:
            logger.warning("No AI API keys configured. AI features will be limited.")
            
        self._client = None 
        # Circuit Breaker State
        self.provider_failures: Dict[str, int] = {}
        self.provider_backoff_until: Dict[str, datetime] = {}
        self.max_failures = 3
        self.backoff_duration_seconds = 60

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-loaded, loop-safe HTTPX client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=Config.AI_REQUEST_TIMEOUT,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5)
            )
        return self._client

    async def _encode_image_to_base64(self, url: str) -> Optional[str]:
        """Download an image from a URL and convert it to a base64 data URI using the pooled client."""
        if not url: return None
        try:
            resp = await self.client.get(url, timeout=10.0)
            if resp.status_code == 200:
                b64_str = base64.b64encode(resp.content).decode('utf-8')
                ext = url.split('.')[-1].lower()
                mime = f"image/{ext}" if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp'] else "image/jpeg"
                return f"data:{mime};base64,{b64_str}"
            logger.warning(f"Failed to fetch image (Status {resp.status_code}): {url}")
        except Exception as e:
            logger.error(f"Error encoding image: {e}")
        return None

    async def close(self):
        """Close the underlying HTTP client"""
        await self.client.aclose()

    async def _call_groq(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000, model_override: Optional[str] = None) -> Optional[str]:
        """Call Groq API (High Performance Llama/Mixtral)"""
        if not self.groq_api_key:
            return None
            
        models_to_try = [model_override] if model_override else [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it"
        ]
        
        for model in models_to_try:
            if not model: continue
            try:
                logger.info(f"Targeting Groq ({model})...")
                response = await self.client.post(
                    self.groq_url,
                    headers={
                        "Authorization": f"Bearer {self.groq_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "response_format": {"type": "json_object"} if "JSON" in prompt.upper() else None
                    }
                )
                    
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if content and str(content).strip():
                        logger.info(f"[SUCCESS] Groq success with {model}")
                        return self._sanitize_ai_response(str(content).strip())
                elif response.status_code == 429:
                    logger.warning(f"Groq 429: Rate limit hit for {model}")
                    continue 
                else:
                    logger.warning(f"Groq {model} failed ({response.status_code}): {response.text[:100]}")
                        
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                logger.error(f"Groq transient error for {model}: {str(e)}")
                raise # Bubble up for tenacity
            except Exception as e:
                logger.error(f"Groq non-transient error: {str(e)}")
                continue
        return None

    async def _call_gemini(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000, model_override: Optional[str] = None, response_schema: Optional[Dict] = None) -> Optional[str]:
        """Call Google Gemini API (multiple variants)"""
        if not self.gemini_api_key:
            return None
            
        models_to_try = [model_override] if model_override else [
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro"
        ]
        
        for model_id in models_to_try:
            if not model_id: continue
            
            full_model_id = model_id if model_id.startswith("models/") else f"models/{model_id}"
            url = f"{self.gemini_url}/{full_model_id}:generateContent"
            
            gen_config = {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "topP": 0.95,
                "topK": 40
            }
            
            if response_schema or "JSON" in prompt.upper():
                gen_config["response_mime_type"] = "application/json"
                if response_schema:
                    gen_config["response_schema"] = response_schema

            try:
                logger.info(f"Targeting Gemini ({model_id})...")
                response = await self.client.post(
                    f"{url}?key={self.gemini_api_key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": gen_config
                    }
                )
                    
                if response.status_code == 200:
                    data = response.json()
                    try:
                        content = data["candidates"][0]["content"]["parts"][0]["text"]
                        if content and str(content).strip():
                            logger.info(f"[SUCCESS] Gemini API success with {model_id}")
                            return self._sanitize_ai_response(str(content).strip())
                    except (KeyError, IndexError, TypeError) as e:
                        logger.warning(f"Gemini parsing failed for {model_id}: {str(e)}")
                        continue
                elif response.status_code == 404:
                    logger.warning(f"Gemini {model_id} not found (404). Trying next...")
                elif response.status_code == 429:
                    logger.warning(f"Gemini {model_id} rate limited (429).")
                    break 
                else:
                    logger.warning(f"Gemini {model_id} failed ({response.status_code}): {response.text[:100]}")
                        
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                logger.error(f"Gemini transient error for {model_id}: {str(e)}")
                raise # Bubble up for tenacity
            except Exception as e:
                logger.error(f"Gemini non-transient error for {model_id}: {str(e)}")
        return None

    async def _call_openrouter(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000, model_override: Optional[str] = None) -> Optional[str]:
        """Call OpenRouter API (fallback)"""
        if not self.openrouter_api_key:
            return None
            
        models_to_try = [model_override] if model_override else [
            "google/gemini-2.0-flash-exp:free",
            "google/gemini-flash-1.5-exp:free",
            "meta-llama/llama-3-8b-instruct:free",
            "openrouter/auto"
        ]
        
        for model in models_to_try:
            if not model: continue
            try:
                if model != models_to_try[0]:
                    await asyncio.sleep(1)
                
                logger.info(f"Targeting OpenRouter ({model})...")
                response = await self.client.post(
                    self.openrouter_url,
                    headers={
                        "Authorization": f"Bearer {self.openrouter_api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://eresumehub.com",
                        "X-Title": "EresumeHub"
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "response_format": {"type": "json_object"} if "JSON" in prompt.upper() else None
                    }
                )
                    
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if content and str(content).strip():
                        logger.info(f"[SUCCESS] OpenRouter success with {model}")
                        return self._sanitize_ai_response(str(content).strip())
                elif response.status_code == 429:
                    logger.warning(f"OpenRouter 429: Rate limit hit for {model}")
                elif response.status_code == 402:
                    logger.warning(f"OpenRouter 402: Payment Required.")
                    break 
                        
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                logger.warning(f"OpenRouter transient error {model}: {str(e)}")
                raise 
            except Exception as e:
                logger.warning(f"OpenRouter {model} failed: {str(e)}")
                continue
        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=6),
        retry=retry_if_exception_type((httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException, httpx.NetworkError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def _execute_with_provider_retry(self, provider_config: str, prompt: str, temperature: float, max_tokens: int) -> Optional[Dict[str, Any]]:
        """Internal retry loop for a specific provider configuration"""
        parts = provider_config.split(":")
        p_name = parts[0].strip().lower()
        p_model = ":".join(parts[1:]).strip() if len(parts) > 1 else None

        now = datetime.now()
        if p_name in self.provider_backoff_until:
            if now < self.provider_backoff_until[p_name]:
                return None

        try:
            result = None
            if p_name == "groq":
                 result = await self._call_groq(prompt, temperature, max_tokens, model_override=p_model)
            elif p_name == "gemini":
                result = await self._call_gemini(prompt, temperature, max_tokens, model_override=p_model)
            elif p_name == "openrouter":
                result = await self._call_openrouter(prompt, temperature, max_tokens, model_override=p_model)
            
            if result:
                self.provider_failures[p_name] = 0
                return {"content": result, "provider": provider_config, "success": True}
        except (httpx.TimeoutException, httpx.NetworkError):
            self.provider_failures[p_name] = self.provider_failures.get(p_name, 0) + 1
            if self.provider_failures[p_name] >= self.max_failures:
                 self.provider_backoff_until[p_name] = now + timedelta(seconds=self.backoff_duration_seconds)
            raise 
        return None

    def _sanitize_ai_response(self, text: str) -> str:
        """Replace problematic Unicode characters with PDF-safe ASCII equivalents"""
        if not text: return ""
        replacements = {
            '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-', '\u2014': '--',
            '\u2015': '--', '\u2017': '_', '\u2018': "'", '\u2019': "'", '\u201a': "'",
            '\u201c': '"', '\u201d': '"', '\u201e': '"', '\u2022': '*', '\u2026': '...',
            '\u2032': "'", '\u2033': '"', '\u00a1': '!', '\u00a0': ' ', '\xad': '-',
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        return text

    async def call_model(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000, request_id: str = "internal") -> Dict[str, Any]:
        """Public AI entry point with provider rotation and circuit-breaking."""
        providers = Config.AI_PROVIDER_ORDER.split(",")
        now = datetime.now()
        
        if Config.AI_TEST_MODE:
            providers = [Config.AI_TEST_PROVIDER]

        for provider_cfg in providers:
            p_name = provider_cfg.split(":")[0] if ":" in provider_cfg else provider_cfg
            backoff_time = self.provider_backoff_until.get(p_name)
            if backoff_time and now < backoff_time:
                continue

            try:
                result = await self._execute_with_provider_retry(provider_cfg, prompt, temperature, max_tokens)
                if result: return result
            except Exception:
                continue
        
        logger.critical(f"[{request_id}] AI Service Exhausted all providers.")
        return {"content": None, "provider": "none", "success": False}

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        """Extract text from an image using Gemini Vision (OCR)."""
        if not self.gemini_api_key: return ""
        try:
            model = "models/gemini-1.5-flash"
            url = f"{self.gemini_url}/{model}:generateContent"
            b64_image = base64.b64encode(image_bytes).decode('utf-8')
            prompt = "Transcribe the text from this resume image exactly. Do not summarize."
            
            response = await self.client.post(
                f"{url}?key={self.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{
                        "parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": b64_image}}]
                    }],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2048}
                }
            )
            if response.status_code == 200:
                return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.error(f"OCR Error: {e}")
        return ""

    async def _get_embedding(self, text: str) -> List[float]:
        """Generate Vector Embedding for text using Gemini."""
        if not text or not str(text).strip() or not self.gemini_api_key:
            return []
        try:
            model = "models/embedding-001"
            url = f"{self.gemini_url}/{model}:embedContent"
            response = await self.client.post(
                f"{url}?key={self.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json={"model": model, "content": {"parts": [{"text": text[:2048]}]}},
                timeout=10.0
            )
            if response.status_code == 200:
                return response.json().get("embedding", {}).get("values", [])
        except Exception as e:
            logger.error(f"Embedding error: {e}")
        return []

    def _cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """Calculate Cosine Similarity between two vectors (Fixed Magnitude B)."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        magnitude_a = math.sqrt(sum(a * a for a in vec_a))
        magnitude_b = math.sqrt(sum(b * b for b in vec_b))
        if magnitude_a == 0 or magnitude_b == 0: return 0.0
        return dot_product / (magnitude_a * magnitude_b)

    async def call_api(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000, request_id: str = "internal") -> Optional[str]:
        """Backward compatible string-only entry point."""
        res = await self.call_model(prompt, temperature, max_tokens, request_id=request_id)
        return res.get("content")

    def _clean_json_string(self, json_str: str) -> str:
        """Robust extraction of JSON from AI responses."""
        if not json_str: return "{}"
        clean_text = re.sub(r'```json\s*|\s*```', '', json_str).strip()
        start_brace = clean_text.find('{')
        start_bracket = clean_text.find('[')
        start = start_brace if (start_bracket == -1 or (start_brace != -1 and start_brace < start_bracket)) else start_bracket
        end = clean_text.rfind('}' if start == start_brace else ']')
        if start == -1 or end == -1 or end < start:
            raise ValueError("No valid JSON found in AI response.")
        return clean_text[start:end+1]

    def _classify_job_scope(self, job_title: str) -> str:
        """Deterministic mapping of job title to primary domain"""
        title_lower = job_title.lower()
        if any(re.search(fr"\b{w}\b", title_lower) for w in ["ai", "ml"]) or any(w in title_lower for w in ["learning", "agent", "llm", "intelligence", "automation"]):
            return "AI / ML"
        if any(w in title_lower for w in ["devops", "platform", "cloud", "infra", "sre"]):
            return "DevOps"
        if any(w in title_lower for w in ["data", "analytics", "warehouse", "bi"]):
            return "Data"
        if any(w in title_lower for w in ["product", "manager", "pm"]):
            return "Product"
        if any(w in title_lower for w in ["design", "figma", "sketch", "prototyping", "wireframe", "typography", "branding", "illustration"]):
            return "Design"
        if any(w in title_lower for w in ["qa", "test", "quality"]):
            return "QA"
        if any(w in title_lower for w in ["security", "cyber", "compliance"]):
            return "Security"
        if any(w in title_lower for w in ["frontend", "ui", "ux", "react", "web developer"]):
            return "Frontend"
        if "backend" in title_lower: return "Backend"
        if "full stack" in title_lower or "fullstack" in title_lower: return "Full-Stack"
        return "Backend"

    def _filter_skills_by_scope(self, skills: List[str], target_scope: str) -> List[str]:
        """Filter skills to include ONLY those relevant to the target scope."""
        if not skills or target_scope not in DOMAIN_SIGNALS: return skills
        target_keywords = DOMAIN_SIGNALS[target_scope]
        filtered = [s for s in skills if any(kw in s.lower() for kw in target_keywords)]
        return filtered if filtered else skills[:5]

    def _infer_summary_domain(self, summary_text: str) -> str:
        """Determine primary domain of summary via weighted signal frequency"""
        text_lower = summary_text.lower()
        scores = {domain: sum(1 for kw in keywords if kw in text_lower) for domain, keywords in DOMAIN_SIGNALS.items()}
        max_score = max(scores.values())
        if max_score == 0: return "Unknown"
        return [d for d, s in scores.items() if s == max_score][0]

    def _validate_summary(self, summary: str, job_title: str, job_description: str = "") -> str:
        """Domain dominance validation for summaries."""
        summary_lower = summary.lower()
        IDENTITY_PHRASES = ["full-stack developer", "fullstack developer", "full stack engineer", "fullstack engineer", "web developer"]
        if any(phrase in summary_lower for phrase in IDENTITY_PHRASES):
            return "SUMMARY_SCOPE_VIOLATION"
        
        target_scope = self._classify_job_scope(job_title)
        domain_scores = {domain: sum(1 for kw in keywords if kw in summary_lower) for domain, keywords in DOMAIN_SIGNALS.items()}
        total_signals = sum(domain_scores.values())
        if total_signals > 0:
            target_percentage = (domain_scores.get(target_scope, 0) / total_signals) * 100
            if target_percentage < 50: return "SUMMARY_SCOPE_VIOLATION"
        return "OK"

    async def analyze_resume(self, resume_text: str, job_role: str, target_country: str, job_description: str = "", parsing_warnings: List[str] = [], request_id: str = "internal") -> Dict[str, Any]:
        """Analyze resume against ATS standards with Hybrid Scoring."""
        try:
            from services.rag_service import RAGService
            rag_data = RAGService.get_complete_rag(target_country, "English")
            rag_context_str = f"TARGET COUNTRY: {target_country}\nCONTEXT: {rag_data['knowledge_base'].get('culture_context', '')}"
        except Exception:
            rag_context_str = get_country_context(target_country)
        
        semantic_score = 50.0
        try:
            vec_resume, vec_jd = await asyncio.gather(self._get_embedding(resume_text[:4000]), self._get_embedding(job_description[:4000] if job_description else job_role))
            if vec_resume and vec_jd: semantic_score = self._cosine_similarity(vec_resume, vec_jd) * 100
        except Exception: pass

        prompt = f"PERSONA: Expert Recruiter in {target_country}. TASK: Analyze resume for '{job_role}'. {rag_context_str}\nRESUME: {resume_text[:8000]}\nJD: {job_description[:2000]}"
        api_res = await self.call_model(prompt, temperature=0.1, max_tokens=1500, request_id=request_id)
        try:
            data = json.loads(self._clean_json_string(api_res.get("content")))
            data["score"] = int((semantic_score * 0.4) + (data.get("qualification_score", 50) * 0.6))
            data["semantic_score"] = round(semantic_score, 1)
            return data
        except Exception:
            return {**get_country_fallback_data(target_country), "score": int(semantic_score), "is_fallback": True}

    def _ensure_list(self, val: Any) -> List[Any]:
        """Defensive Normalization (v6.4.0): Force non-list types into lists."""
        if val is None: return []
        if isinstance(val, list): return val
        if isinstance(val, str) and val.strip(): return [val.strip()]
        return []

    async def extract_structured_data(self, resume_text: str, request_id: str = "internal") -> Dict[str, Any]:
        """Extract resume text into structured JSON with production-grade hardening. (v6.4.0)"""
        extraction_schema = {
            "full_name": "string",
            "email": "string",
            "phone": "string",
            "headline": "string (e.g. Senior Software Engineer)",
            "summary": "string (professional summary)",
            "work_experiences": [{"job_title": "str", "company": "str", "start_date": "str", "end_date": "str", "is_current": "bool", "achievements": ["str"]}],
            "educations": [{"degree": "str", "institution": "str", "graduation_date": "str"}],
            "skills": ["string"]
        }
        
        prompt = f"""
        TASK: Parse the following RESUME TEXT into the EXACT JSON schema provided below.
        
        RULES:
        1. Extract the 'full_name' with absolute priority. If not explicitly found, look at the header or email prefix.
        2. Format all dates as 'YYYY-MM' (e.g., '2023-01').
        3. Ensure 'achievements' in work experience is a LIST of strings, NOT a single paragraph.
        4. If a field is missing, return null or an empty list [].
        
        SCHEMA: {json.dumps(extraction_schema)}
        
        TEXT:
        {resume_text[:12000]}
        """
        
        api_res = await self.call_api(prompt, temperature=0.0, max_tokens=3500, request_id=request_id)
        
        try:
            raw_json = json.loads(self._clean_json_string(api_res))
            
            # Map common legacy keys to Pydantic model keys
            mapped_json = copy.deepcopy(raw_json)
            if "experience" in raw_json and "work_experiences" not in raw_json:
                mapped_json["work_experiences"] = raw_json["experience"]
            if "education" in raw_json and "educations" not in raw_json:
                mapped_json["educations"] = raw_json["education"]
                
            # DEFENSIVE NORMALIZATION (v6.4.0): Prevent Pydantic Type Conflicts
            # We force problematic keys to be lists before validation.
            list_keys = ["work_experiences", "educations", "skills"]
            for k in list_keys:
                mapped_json[k] = self._ensure_list(mapped_json.get(k))

            # FUZZY NAME RESOLUTION (v6.3.0): Prevent 422 if AI fails
            if not mapped_json.get("full_name") and mapped_json.get("email"):
                name_part = mapped_json["email"].split('@')[0].replace('.', ' ').title()
                mapped_json["full_name"] = name_part
            
            # Final Fallback: Mandatory Field (v6.3.0)
            if not mapped_json.get("full_name"):
                 mapped_json["full_name"] = "Resume Professional"
                
            # Validate and coerce types (Staff+ Robustness)
            validated = ExtractionResponse(**mapped_json).model_dump()
            
            # Additional cleanup: Rename keys for the ProfileService
            return {
                **validated,
                "experience": validated["work_experiences"], # Backward compatibility
                "education": validated["educations"]
            }
        except Exception as e:
            logger.error(f"Structured extraction validation crash: {e}. RAW OUTPUT: {api_res[:200]}")
            return {"full_name": "Resume Professional", "work_experiences": [], "educations": [], "skills": []}

    async def generate_resume_title(self, user_data: Dict[str, Any], job_description: str = "", request_id: str = "internal") -> str:
        """Suggest a concise resume title."""
        role = user_data.get("headline", "Professional")
        prompt = f"Suggest a 3-5 word resume title for a {role} applying to: {job_description[:400]}. Return ONLY title text."
        res = await self.call_api(prompt, temperature=0.7, max_tokens=30, request_id=request_id)
        return str(res).strip() if res else f"{role} Resume"

    async def generate_tailored_resume(self, user_data: Dict[str, Any], job_description: str, country: str, language: str, job_title: str, rag_data: Dict[str, Any] = None, request_id: str = "internal") -> Dict[str, Any]:
        """Tailor resume content to match a specific job description with schema enforcement."""
        schema = {
            "generated_summary": "string (strictly optimize professional summary for this ATS and role)",
            "headline": "string (professional headline strictly matching requested role)",
            "experience": [{"job_title": "str", "company": "str", "description": ["str"], "achievements": ["str"]}],
            "projects": [{"title": "str", "description": ["str"]}],
            "education": [{"degree": "str", "institution": "str"}],
            "skills": ["string"]
        }
        
        language_template_json = json.dumps(rag_data.get("language_template", {})) if rag_data else "{}"
        knowledge_base_json = json.dumps(rag_data.get("knowledge_base", {})) if rag_data else "{}"
        cv_structure_order = json.dumps(rag_data.get("knowledge_base", {}).get("cv_structure", {}).get("order", [])) if rag_data else "[]"
        
        compliance_injection = ""
        if country == "Germany":
            missing = []
            if not user_data.get("date_of_birth"): missing.append("Date of Birth")
            if not user_data.get("nationality"): missing.append("Nationality")
            
            has_german = False
            for lang_entry in user_data.get("languages", []):
                lang_str = str(lang_entry).lower()
                if "german" in lang_str: has_german = True
            if not has_german: missing.append("German Language Proficiency (CEFR level)")
            
            if missing:
                compliance_injection = f"\n        COMPLIANCE GAP (ADAPT REQUIRED):\n        The user is applying in Germany but is missing: {', '.join(missing)}. Create the resume structure as usual, but do not invent these fields. Ensure the rest of the resume is exceptionally strong to compensate."

        prompt = f"""
        SYSTEM ROLE: You are an expert {country} CV writer.
        {compliance_injection}
        
        You MUST follow ALL rules below.
        If ANY rule is violated → response is INVALID.
        
        VALIDATION RULES:
        - The resume MUST match the job title EXACTLY: '{job_title}'
        - DO NOT change or generalize the role
        - DO NOT fallback to generic roles (e.g. laborer)
        
        🚨 STRICT RULE ON WEAK VERBS:
        DO NOT use any of the following:
        - Helped
        - Contributing
        - Assisted
        - Participated
        If present → REWRITE automatically using strong ownership verbs ("Architected", "Engineered", "Spearheaded").
        
        🚨 STRICT RULE ON METRICS:
        Every bullet MUST include:
        - numbers
        - scale
        - impact
        If unknown → estimate realistically based on the role and industry.
        Example: "Built backend API" (WRONG) -> "Built backend API handling 5K+ monthly users" (CORRECT).
        
        - CONCURRENT ROLES: If the user has multiple overlapping 'Present' roles, clarify them (e.g. mark one as 'Consulting' or 'Part-time') to avoid recruiter friction.
        
        INPUT DATA: {json.dumps(user_data)}
        JOB DESCRIPTION: {job_description[:1500]}
        
        LANGUAGE RULES (Headings, Verbs):
        {language_template_json}
        
        MARKET RULES:
        {knowledge_base_json}
        
        STRUCTURE ORDER:
        {cv_structure_order}
        
        OUTPUT REQUIREMENTS:
        - Output MUST be a valid JSON object matching the schema below.
        - NEVER combine words artificially (e.g. "highimpact" MUST be "high-impact"). Maintain strict spelling and punctuation.
        - Use {country}-specific professional terminology and ATS keywords.
        - Use {language} section headings (based on LANGUAGE RULES).
        - Follow {country} tone (formal, no pronouns if applicable).
        - Place Professional Summary at TOP.
        - Use action verbs from provided list where applicable.
        - Include metrics in bullet points.
        - Do NOT invent data; only refine and re-structure the existing experience.
        - Return ONLY the JSON object.
        
        SCHEMA: {json.dumps(schema)}
        """
        
        logger.info(f"[{request_id}] JOB TITLE EXECUTED: '{job_title}'")
        logger.info(f"[{request_id}] RAG DATA USED: Language={len(language_template_json)} bytes, KB={len(knowledge_base_json)} bytes")
        logger.info(f"[{request_id}] FINAL PROMPT LENGTH: {len(prompt)} chars")
        
        api_res = await self.call_model(prompt, temperature=0.4, max_tokens=3000, request_id=request_id)
        
        if not api_res.get("success"):
            logger.error(f"[{request_id}] AI Provider Exhausted in generate_tailored_resume.")
            return {"success": False, "error": "PROVIDER_FAIL"}

        try:
            content = api_res.get("content")
            clean_json = self._clean_json_string(content)
            tailored = json.loads(clean_json)
            
            # Defensive normalization: Ensure Pydantic types (v16.4.14)
            tailored["experience"] = self._ensure_list(tailored.get("experience"))
            tailored["projects"] = self._ensure_list(tailored.get("projects"))
            tailored["education"] = self._ensure_list(tailored.get("education"))
            tailored["skills"] = self._ensure_list(tailored.get("skills"))
            
            # Map back to original structure
            return {
                "success": True, 
                "resume_content": {**user_data, **tailored}, 
                "generated_summary": tailored.get("generated_summary", "")
            }
        except Exception as e:
            logger.error(f"[{request_id}] AI Parse Failure: {e}. Content: {api_res.get('content')[:150]}")
            return {"success": False, "error": "PARSE_ERROR"}

    async def enforce_compliance_correction(self, json_payload: Dict[str, Any], violations: List[str], country: str = "Germany", request_id: str = "correction") -> Dict[str, Any]:
        """Force AI to fix specific compliance violations in the generated JSON via FULL regeneration."""
        prompt = f"""
        SYSTEM ROLE: You are an expert {country} CV auditor and re-writer.
        
        TASK:
        The following JSON resume content violates strict compliance rules for the {country} market.
        You must RE-GENERATE the ENTIRE JSON resume content from scratch to be 100% compliant.
        
        VIOLATIONS TO FIX:
        {chr(10).join(['- ' + v for v in violations])}
        
        STRICT RE-WRITE RULES:
        1. FULL REGENERATION: Do NOT patch partial fields. Re-generate the WHOLE JSON document for perfect tone and section coherence.
        2. METRICS: Every single bullet point MUST now have a number, percentage, or specific scale.
        3. VERBS: Absolutely NO "Helped", "Contributing", or "Assisted". Use "Spearheaded", "Directed", "Executed", "Engineered".
        4. MANDATORY SECTIONS: Ensure a Language section exists with CEFR levels (A1-C2).
        5. EDUCATION: Remove any education below Bachelor's level (e.g., High School/Pre-University).
        
        INPUT JSON:
        {json.dumps(json_payload)}
        
        Return ONLY the corrected JSON object.
        """
        
        api_res = await self.call_model(prompt, temperature=0.1, max_tokens=3000, request_id=request_id)
        
        if not api_res.get("success"):
            return json_payload # Fallback to original if correction fails
            
        try:
            content = api_res.get("content")
            clean_json = self._clean_json_string(content)
            return json.loads(clean_json)
        except Exception as e:
            logger.error(f"[{request_id}] AI Correction Parse Failure: {e}")
            return json_payload

# Global instance
ai_service = AIService()
