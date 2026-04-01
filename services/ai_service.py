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

load_dotenv()

logger = logging.getLogger(__name__)

from datetime import datetime, timedelta
import difflib
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from configurations.countries import get_country_context, get_country_fallback_data
from app_settings import Config

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

# (encode_image_to_base64 moved to AIService Class)

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
        # Use verify_ssl=False for local dev if needed, or standard client
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
            
        # Default to Llama 3 70B (Versatile) or Mixtral
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
            
        # Try models in order of preference (newest/best first)
        models_to_try = [model_override] if model_override else [
            "gemini-2.0-flash",          # User requested model (Priority 1)
            "gemini-1.5-flash",
            "gemini-1.5-pro"
        ]
        
        for model_id in models_to_try:
            if not model_id: continue
            
            # Ensure model_id has models/ prefix
            full_model_id = model_id if model_id.startswith("models/") else f"models/{model_id}"
            url = f"{self.gemini_url}/{full_model_id}:generateContent"
            
            # Prepare generation config
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
                            # Character Sanitization for PDF rendering
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
            
        # Updated Model List (Removing 404s)
        models_to_try = [model_override] if model_override else [
            "google/gemini-2.0-flash-exp:free",
            "google/gemini-flash-1.5-exp:free",
            "meta-llama/llama-3-8b-instruct:free",
            "microsoft/phi-3-medium-128k-instruct:free",
            "huggingfaceh4/zephyr-7b-beta:free",
            "openrouter/auto"
        ]
        
        for model in models_to_try:
            if not model: continue
            try:
                if model != models_to_try[0]:
                    await asyncio.sleep(1) # Small delay between retries
                
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
                        # Character Sanitization for PDF rendering
                        return self._sanitize_ai_response(str(content).strip())
                elif response.status_code == 429:
                    logger.warning(f"OpenRouter 429: Rate limit hit for {model}")
                elif response.status_code == 402:
                    logger.warning(f"OpenRouter 402: Payment Required.")
                    break 
                        
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                logger.warning(f"OpenRouter transient error {model}: {str(e)}")
                raise # Bubble up for tenacity
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

        # 1. Circuit Breaker Check
        now = datetime.now()
        if p_name in self.provider_backoff_until:
            if now < self.provider_backoff_until[p_name]:
                logger.warning(f"CIRCUIT BREAKER: Skipping {p_name} until {self.provider_backoff_until[p_name].strftime('%H:%M:%S')}")
                return None

        # 2. Execution
        try:
            result = None
            if p_name == "groq":
                 result = await self._call_groq(prompt, temperature, max_tokens, model_override=p_model)
            elif p_name == "gemini":
                result = await self._call_gemini(prompt, temperature, max_tokens, model_override=p_model)
            elif p_name == "openrouter":
                result = await self._call_openrouter(prompt, temperature, max_tokens, model_override=p_model)
            
            if result:
                # SUCCESS: Reset Circuit Breaker for this provider
                self.provider_failures[p_name] = 0
                return {
                    "content": result,
                    "provider": provider_config,
                    "success": True
                }
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            # FAILURE: Increment Circuit Breaker
            self.provider_failures[p_name] = self.provider_failures.get(p_name, 0) + 1
            if self.provider_failures[p_name] >= self.max_failures:
                 self.provider_backoff_until[p_name] = now + timedelta(seconds=self.backoff_duration_seconds)
                 logger.critical(f"CIRCUIT BREAKER TRIGGERED for {p_name} after {self.max_failures} failures.")
            raise # Propagate for Tenacity retry within window

        return None

    def _sanitize_ai_response(self, text: str) -> str:
        """Replace problematic Unicode characters with PDF-safe ASCII equivalents"""
        if not text:
            return ""
        replacements = {
            '\u2010': '-', # Hyphen
            '\u2011': '-', # Non-breaking hyphen
            '\u2012': '-', # Figure dash
            '\u2013': '-', # En dash
            '\u2014': '--',# Em dash
            '\u2015': '--',# Horizontal bar
            '\u2017': '_', # Double low line
            '\u2018': "'", # Left single quotation mark
            '\u2019': "'", # Right single quotation mark
            '\u201a': "'", # Single low-9 quotation mark
            '\u201c': '"', # Left double quotation mark
            '\u201d': '"', # Right double quotation mark
            '\u201e': '"', # Double low-9 quotation mark
            '\u2022': '*', # Bullet
            '\u2026': '...', # Ellipsis
            '\u2032': "'", # Prime
            '\u2033': '"', # Double prime
            '\u00a1': '!', # Inverted exclamation mark
            '\u00a0': ' ', # Non-breaking space
            '\xad': '-',    # Soft hyphen
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        return text

    async def _call_api_rich(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> Dict[str, Any]:
        """Rich API entry point with provider rotation and audit tracking"""
        providers = Config.AI_PROVIDER_ORDER.split(",")
        
        if Config.AI_TEST_MODE:
            providers = [Config.AI_TEST_PROVIDER]
            logger.info(f"AI_TEST_MODE enabled. Forcing provider: {providers[0]}")

        for provider_cfg in providers:
            try:
                # Removed wait_for wrapper to prevent collision with Tenacity retries
                # Individual helper timeouts + Tenacity are more robust.
                result = await self._execute_with_provider_retry(provider_cfg, prompt, temperature, max_tokens)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Provider {provider_cfg} exhausted or timed out: {e}")
                continue
        
        logger.critical("AI Service Exhausted all providers and retries.")
        return {"content": None, "provider": "none", "success": False}

    async def extract_text_from_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        """
        Extract text from an image using Gemini Vision (OCR Fallback).
        """
        if not self.gemini_api_key:
            logger.warning("Cannot perform OCR: No Gemini API Key.")
            return ""
            
        try:
            # Use Gemini 1.5 Flash for fast, cheap vision
            model = "models/gemini-1.5-flash"
            url = f"{self.gemini_url}/{model}:generateContent"
            
            # Encode image to base64
            b64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            prompt = "Transcribe the text from this resume image exactly. Do not summarize. Maintain layout structure where possible."
            
            response = await self.client.post(
                f"{url}?key={self.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": b64_image
                                }
                            }
                        ]
                    }],
                    "generationConfig": {
                        "temperature": 0.0, # Deterministic for OCR
                        "maxOutputTokens": 2048
                    }
                },
                timeout=30.0
            )
                
            if response.status_code == 200:
                data = response.json()
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    logger.info("Gemini OCR successful.")
                    return text
                except (KeyError, IndexError):
                    logger.error("Gemini OCR response parsing failed.")
            else:
                logger.error(f"Gemini OCR failed ({response.status_code}): {response.text[:100]}")
                    
        except Exception as e:
            logger.error(f"OCR Error: {e}")
            
        return ""

    async def _get_embedding(self, text: str) -> List[float]:
        """
        Generate Vector Embedding for text using Gemini (Primary) or OpenRouter (Fallback).
        "Generation 4" Vector Space Model.
        """
        if not text or not str(text).strip():
            return []

        # 1. Try Gemini Embeddings (text-embedding-004)
        if self.gemini_api_key:
            try:
                # Use embedding-001 (Legacy/Stable) as 004 is 404ing for some keys
                model = "models/embedding-001"
                url = f"{self.gemini_url}/{model}:embedContent"
                
                response = await self.client.post(
                    f"{url}?key={self.gemini_api_key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "model": model,
                        "content": {"parts": [{"text": text[:2048]}]} 
                    },
                    timeout=10.0
                )
                    
                if response.status_code == 200:
                    data = response.json()
                    embedding = data.get("embedding", {}).get("values")
                    if embedding:
                        return embedding
                else:
                    logger.warning(f"Gemini embedding failed ({response.status_code}): {response.text[:100]}")
            except Exception as e:
                logger.error(f"Gemini embedding error: {e}")

        # 2. Fallback? OpenRouter doesn't always support embeddings cleanly across models.
        # For this MVP phase, if Gemini fails, we might return empty list -> Resulting in 0 semantic score.
        # Or we could implement a local fallback if we had sentence-transformers, but we don't.
        
        logger.warning("Embedding generation failed. Returning empty vector.")
        return []

    def _cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """
        Calculate Cosine Similarity between two vectors using standard math.
        Formula: (A . B) / (||A|| * ||B||)
        """
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
            
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        magnitude_a = math.sqrt(sum(a * a for a in vec_a))
        magnitude_b = math.sqrt(sum(b * b for b in vec_b))
        
        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0
            
        return dot_product / (magnitude_a * magnitude_b)

    async def _call_api(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> Optional[str]:
        """Backward compatible string-only entry point"""
        res = await self._call_api_rich(prompt, temperature, max_tokens)
        return res.get("content")

    @staticmethod
    async def call_ai_api(prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> str:
        """Static wrapper for backward compatibility"""
        from services.ai_service import ai_service
        return await ai_service._call_api(prompt, temperature, max_tokens)


    def _clean_json_string(self, json_str: str) -> str:
        """Robust extraction of JSON from AI responses (handles both {} and [] root objects)."""
        clean_text = re.sub(r'```json\s*|\s*```', '', json_str).strip()
        
        # Check for first and last valid JSON boundaries
        start_brace = clean_text.find('{')
        start_bracket = clean_text.find('[')
        
        # Pick the one that appears first
        start = -1
        if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
            start = start_brace
        else:
            start = start_bracket
            
        # Find corresponding end
        end = clean_text.rfind('}' if start == start_brace else ']')
        
        if start == -1 or end == -1 or end < start:
            logger.error(f"No valid JSON block found in response: {json_str[:500]}...")
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
             # "Web Developer" often skews frontend unless specified otherwise
            return "Frontend"
        if "backend" in title_lower:
            return "Backend"
        if "full stack" in title_lower or "fullstack" in title_lower:
            return "Full-Stack"
            
        return "Backend" # Default fallback for software engineering
    
    def _filter_skills_by_scope(self, skills: List[str], target_scope: str) -> List[str]:
        """
        Filter skills to include ONLY those relevant to the target scope.
        """
        if not skills or target_scope not in DOMAIN_SIGNALS:
            return skills
        
        target_keywords = DOMAIN_SIGNALS[target_scope]
        filtered = []
        
        for skill in skills:
            skill_lower = skill.lower()
            # Include if skill contains any target keyword
            if any(kw in skill_lower for kw in target_keywords):
                filtered.append(skill)
        
        # Return filtered skills, or top 5 if none match
        return filtered if filtered else skills[:5]

    def _infer_summary_domain(self, summary_text: str) -> str:
        """Determine primary domain of summary via weighted signal frequency"""
        text_lower = summary_text.lower()
        scores = {domain: 0 for domain in DOMAIN_SIGNALS}
        
        for domain, keywords in DOMAIN_SIGNALS.items():
            for kw in keywords:
                if kw in text_lower:
                    scores[domain] += 1
        
        # Return dominant domain, or "Unknown" if no signals
        max_score = max(scores.values())
        if max_score == 0:
            return "Unknown"
            
        # Get all domains with max score
        top_domains = [d for d, s in scores.items() if s == max_score]
        return top_domains[0]

    def _validate_summary(self, summary: str, job_title: str, job_description: str) -> str:
        """
        Section-aware, dominance-based scope validation.
        Validates ONLY the Professional Summary for identity control.
        Returns 'OK' if valid, or error code if invalid.
        """
        summary_lower = summary.lower()
        
        # 1. STRICT: Block explicit cross-domain identity phrases (always forbidden)
        # These are role labels that explicitly claim a different identity
        IDENTITY_PHRASES = [
            "full-stack developer", "fullstack developer",
            "full stack engineer", "fullstack engineer",
            "end-to-end developer",
            "web developer" # Only as explicit identity, not as context
        ]
        
        for phrase in IDENTITY_PHRASES:
            if phrase in summary_lower:
                logger.warning(f"Summary validation failed: Explicit identity phrase '{phrase}' found.")
                return "SUMMARY_SCOPE_VIOLATION"
        
        # 2. DOMINANCE-BASED: Check if target domain signals dominate (>60%)
        target_scope = self._classify_job_scope(job_title)
        
        # Count all domain signals in the summary
        domain_scores = {domain: 0 for domain in DOMAIN_SIGNALS}
        for domain, keywords in DOMAIN_SIGNALS.items():
            for kw in keywords:
                if kw in summary_lower:
                    domain_scores[domain] += 1
        
        total_signals = sum(domain_scores.values())
        
        # If there are signals, check dominance
        if total_signals > 0:
            target_score = domain_scores.get(target_scope, 0)
            target_percentage = (target_score / total_signals) * 100
            
            # Require >50% dominance for the target domain (relaxed from 60%)
            if target_percentage < 50:
                # Find what domain is actually dominating
                dominant_domain = max(domain_scores, key=domain_scores.get)
                dominant_score = domain_scores[dominant_domain]
                dominant_percentage = (dominant_score / total_signals) * 100
                
                logger.warning(
                    f"Summary validation failed: Target domain '{target_scope}' has {target_percentage:.1f}% signals, "
                    f"but '{dominant_domain}' dominates with {dominant_percentage:.1f}%. Require >50% for target domain."
                )
                return "SUMMARY_SCOPE_VIOLATION"
        
        # 3. CONTEXT-AWARE: Allow mentions of other domains as context (not identity)
        # Examples that are OK:
        # - "AI Engineer with experience applying ML to web applications"
        # - "Built automation systems within production software environments"
        # These contain cross-domain keywords but don't claim cross-domain identity
        
        return "OK"


    async def analyze_resume(self, resume_text: str, job_role: str, target_country: str, job_description: str = "", parsing_warnings: List[str] = []) -> Dict[str, Any]:
        """
        Analyze resume against ATS standards with Country-Specific Logic
        """
        
        # 1. Get Country Context from RAG (Single Source of Truth)
        knowledge_base = {}
        try:
            from services.rag_service import RAGService
            rag_data = RAGService.get_complete_rag(target_country, "English")
            knowledge_base = rag_data.get("knowledge_base", {})
            
            # Construct specific RAG context
            rag_context_str = (
                f"TARGET COUNTRY: {target_country}\n"
                f"CULTURAL CONTEXT: {knowledge_base.get('culture_context', '')}\n"
                f"FORMATTING RULES:\n"
                f"- Max Pages: {knowledge_base.get('cv_structure', {}).get('max_pages', 2)}\n"
                f"- Photo Required: {knowledge_base.get('formatting', {}).get('photo_required', False)}\n"
                f"- Date Format: {knowledge_base.get('formatting', {}).get('date_format', 'MM/YYYY')}\n"
                f"- Layout: {knowledge_base.get('formatting', {}).get('layout', 'Standard')}\n"
                f"KEY SECTIONS: {json.dumps(knowledge_base.get('sections', {}))}\n"
                f"STRENGTHS TO LOOK FOR: {json.dumps(knowledge_base.get('strengths', []))}\n"
                f"WARNINGS TO CHECK: {json.dumps(knowledge_base.get('warnings', []))}"
            )
        except (ImportError, Exception) as e:
            logger.warning(f"RAG Load in analysis failed: {e}")
            rag_context_str = get_country_context(target_country)
        
        # 1. Generate Vectors & Calculate Semantic Score (Parallel to save time)
        # We need embeddings for Resume and Job Description
        semantic_score = 0.0
        try:
            # Prepare texts (Truncate to focus on core content for embedding)
            if isinstance(resume_text, dict):
                vec_resume_text = json.dumps(resume_text, ensure_ascii=False)[:3000]
            else:
                vec_resume_text = str(resume_text)[:3000]
                
            vec_jd_text = str(job_description)[:3000] if job_description and len(job_description) > 50 else str(job_role)
            
            logger.info("Generating parallel vectors for Hybrid Scoring...")
            vec_resume_task = self._get_embedding(vec_resume_text)
            vec_jd_task = self._get_embedding(vec_jd_text)
            
            vec_resume, vec_jd = await asyncio.gather(vec_resume_task, vec_jd_task)
            
            if not vec_resume or not vec_jd:
                logger.warning("Embedding system failure (Empty Vectors). Falling back to neutral semantic score.")
                semantic_score = 50.0 # Neutral baseline
            else:
                similarity = self._cosine_similarity(vec_resume, vec_jd)
                semantic_score = similarity * 100
                logger.info(f"Hybrid Scoring: Semantic={semantic_score:.1f}")
            
        except Exception as vec_error:
            logger.error(f"Vector calculation failed: {vec_error}")
            semantic_score = 50.0 # Neutral baseline
            
        # 1. Extract RAG Rules (Dynamic Injection)
        kbase = knowledge_base if knowledge_base else {}
        ats_rules = kbase.get('ats_optimization', {})
        
        must_haves = kbase.get('ats_optimization', {}).get('must_have', [])
        deal_breakers = kbase.get('hiring_psychology', {}).get('dealbreakers', [])
        # Also check 'warnings' from knowledge base root or ats_optimization 'avoid'
        formatting_no_gos = ats_rules.get('avoid', [])

        # Fallback values if empty (safety net)
        if not must_haves: must_haves = ["Clear structure", "Keywords"]
        if not deal_breakers: deal_breakers = ["Spelling errors"]
        if not formatting_no_gos: formatting_no_gos = ["Graphics", "Tables"]

        # 2. Call AI with Updated Prompt for Hybrid Logic
        warnings_str = "\n".join([f"- {w}" for w in parsing_warnings]) if parsing_warnings else "None detected."
        
        prompt = f"""
You are an expert ATS (Applicant Tracking System) analyzer with 20 years of HR experience in {target_country}.
Analyze this resume for the role of "{job_role}".

DETECTED ISSUES (AUTOMATED CHECKS):
{warnings_str}
If "SUSPICIOUS_FORMATTING_DETECTED" is present, you MUST penalize the score significantly (-20 points) and flag it as a critical warning.
If "OCR Failed" is present, note that the text might be garbage.

STRICT RULES (CRITICAL):
1. [METADATA] TRUST THE METADATA HEADER.
   - If "[METADATA] Contains Photos/Images: True", DO NOT say "Missing photo".
   - If "[METADATA] Detected Page Count: 1", DO NOT say "Resume is too long".
   
2. COUNTRY SPECIFIC RULES ({target_country}):
{rag_context_str}

[TASK: FORENSIC COUNTRY ANALYSIS]
You are an expert recruiter for {target_country}. 
Analyze the resume against these STRICT local standards:

LOCAL RULES (DO NOT IGNORE):
- MUST HAVE: {", ".join(must_haves)}
- IMMEDIATE REJECTION IF: {", ".join(deal_breakers)}
- FORMATTING VIOLATIONS: {", ".join(formatting_no_gos)}

RESUME CONTENT:
{resume_text[:6000]}

JOB DESCRIPTION:
{job_description[:1000] if job_description else "Standard industry requirements for this role."}

TASK:
1. Evaluate "Qualification Score" (0-100) based on HARD REQUIREMENTS (Visa, Degree, Years of Exp, Must-Have Skills).
2. Identify 3 key strengths.
3. Identify 3 critical warnings/improvements.
4. Check explicitly for {target_country} violations.
5. Extract keywords found and missing.
6. [TASK: TOP FIX GENERATION]
   - Identify the single most damaging violation of the injected LOCAL RULES.
   - The explanation MUST be authoritative.

SCORING FORMULA (Hybrid Consine-LLM):
- You provide the 'Qualification Score' (40% Weight) -> How well do they meet specific hard requirements?
- The System provides 'Semantic Score' (60% Weight) -> Vector-based meaning match (Calculated separately).

Return ONLY valid JSON with this structure:
{{
    "qualification_score": 75,  <-- SCORE BASED ON HARD REQS ONLY
    "score_breakdown": {{
        "keywords": 20,
        "formatting": 15,
        "impact": 25,
        "tone": 15
    }},
    "top_fix": {{
        "title": "CITE THE SPECIFIC RULE (e.g., 'German CVs must not use Tables')",
        "current": "Quote the part of the resume violating this",
        "suggested": "The rewritten version",
        "reasoning": "EXPLAIN THE CONSEQUENCE. (e.g., 'ATS parsers cannot read tables, causing your application to appear empty. Change to bullets immediately.')",
        "points": 20
    }},
    "strengths": ["Clear section headers", "Good use of metrics"],
    "warnings": ["Missing tech stack summary", "Resume is too long for {target_country}"],
    "errors": ["Missing contact info"],
    "countrySpecific": ["Add photo for {target_country}", "Use tabular format"],
    "keywords": {{"found": 12, "recommended": 15, "missing": ["Skill A", "Skill B"]}}
}}
"""
        
        # 2. Call AI with Retry
        result = await self._call_api(prompt, temperature=0.2, max_tokens=1500)
        
        # 3. Handle Complete Failure (Fallback)
        if not result:
            logger.error("ATS Analysis failed completely. Using Static Fallback.")
            fallback_data = get_country_fallback_data(target_country)
            
            return {
                "score": 0, # Explicit 0 to indicate failure/draft
                "country": target_country,
                "jobRole": job_role,
                "strengths": fallback_data["strengths"],
                "warnings": ["AI Service Unavailable - Showing static country advice"],
                "errors": ["Could not connect to AI service"],
                "countrySpecific": fallback_data["countrySpecific"],
                "keywords": {"found": 0, "recommended": 0, "missing": []},
                "is_fallback": True
            }
            
        try:
            # 2. Parsing & Pydantic Validation
            try:
                json_str = self._clean_json_string(result)
                validated_data = ATSAnalysisResponse.model_validate_json(json_str)
                data = validated_data.model_dump()
            except (ValidationError, Exception) as e:
                logger.error(f"ATS Parse/Validation Error: {e} | Content: {result[:200]}")
                fallback_data = get_country_fallback_data(target_country)
                return {
                    "score": 0,
                    "country": target_country,
                    "jobRole": job_role,
                    "strengths": fallback_data["strengths"],
                    "warnings": [f"AI Validation Error: {str(e)[:50]}..."],
                    "errors": ["Schema validation failed. Technical details logged."],
                    "countrySpecific": fallback_data["countrySpecific"],
                    "keywords": {"found": 0, "recommended": 0, "missing": []},
                    "is_fallback": True
                }

            # Ensure required fields
            data["country"] = data.get("country") or target_country
            data["jobRole"] = data.get("jobRole") or job_role
            
            # === HYBRID SCORING LOGIC ===
            # Fetch qualification score from LLM (default to 50 if missing)
            qual_score = data.get("qualification_score", data.get("score", 50))
            
            # Calculate Final Weighted Score
            # 60% Semantic (Vector) + 40% Qualification (LLM)
            final_score = (semantic_score * 0.6) + (qual_score * 0.4)
            data["score"] = max(0, min(100, int(final_score)))
            
            # Inject breakdown for frontend visibility
            data["scoring_method"] = "Resume2Vec (Hybrid)"
            data["semantic_score"] = round(semantic_score, 1)
            data["qualification_score"] = qual_score
            
            data["is_fallback"] = False
            
            logger.info(f"[SUCCESS] ATS Analysis complete: Score = {data['score']}")
            return data
            
        except Exception as e:
            logger.error(f"Failed to parse ATS JSON: {str(e)} | Content: {result[:200]}...")
            
            # Partial success fallback (if we got text but not JSON)
            fallback_data = get_country_fallback_data(target_country)
            return {
                "score": 40,
                "country": target_country,
                "jobRole": job_role,
                "strengths": ["Resume text was readable"],
                "warnings": ["Analysis parsing failed - Check formatting"],
                "errors": [str(e)],
                "countrySpecific": fallback_data["countrySpecific"],
                "is_fallback": True
            }

    async def extract_structured_data(self, resume_text: str) -> Dict[str, Any]:
        """Parse raw resume text into structured profile format"""
        # Truncate to avoid context limit, but keep enough for full resume
        # increased to 12000 to ensure we capture education at the bottom
        safe_text = resume_text[:12000] 
        
        prompt = f"""
You are an expert Resume Parser. Your job is to extract unstructured text into a valid JSON profile.

CRITICAL RULES:
1. **ABSOLUTE VERBATIM MODE**: 
   - **Do NOT** invent introductions like "Proven ability to...".
   - **Do NOT** expand short descriptions. If the resume says "Completed MERN Stack projects", YOU MUST RETURN EXACTLY THAT.
   - **Do NOT** split paragraphs into bullet points. If the input is a paragraph, return it as a SINGLE string in `achievements`.

2. **DATE LOOKUP STRATEGY**: 
   - **Look Upwards**: If a project has no date, **LOOK AT THE HEADER ABOVE IT**.
     - Example: "PROJECTS (2023 - Present)" -> All projects below this line inherit "2023-01" as Start Date.
   - **This is MANDATORY**: It is better to specific a Section Date than to return null.

3. **SECTION SEPARATION**:
   - **Work Experience**: Companies/Employment only.
   - **Projects**: Academic/Personal projects.

4. **"Current" Handling**: If date is "Current" or "Present", set `"is_current": true` and `"end_date": null`.

Return ONLY valid JSON with this structure:

{{
    "full_name": "Name",
    "headline": "Professional Title (e.g. Senior UI Designer)",
    "email": "email@example.com",
    "phone": "Phone",
    "street_address": "Street Name and Number (e.g. 'Musterstraße 14')",
    "postal_code": "ZIP/Postal Code (e.g. '10117')",
    "city": "City",
    "country": "Country",
    "nationality": "Nationality (Extract from 'Identity' or 'Personal' section)",
    "date_of_birth": "YYYY-MM-DD (Extract from 'Identity', 'Born', or 'Date of Birth')",
    "links": [{{"label": "Portfolio", "url": "https://..."}}],
    "summary": "Professional summary",
    "skills": ["Skill1", "Skill2"],
    "languages": [{{"name": "Language", "level": "Proficiency Level (Preserve exact text like 'Native' or 'JLPT N4' or 'C2')"}}],
    "certifications": [{{
        "name": "Certification Name",
        "issuing_organization": "Issuer (if available)",
        "issue_date": "YYYY-MM (if available)"
    }}],
    // EXTRACTION NOTES:
    // - If input has "Identity: Born 15.05.1988 | Nationality: German", SPLIT it into date_of_birth and nationality.
    // - If input has "Address: Musterstraße 14, 10117 Berlin", SPLIT into street_address (Musterstraße 14), postal_code (10117), city (Berlin).
    // - LANGUAGE LEVELS: Preserve what the user wrote exactly.
    "work_experiences": [{{
        "job_title": "Role",
        "company": "Company Name",
        "city": "City",
        "country": "Country",
        "location": "Full Location String",
        "start_date": "YYYY-MM",
        "end_date": "YYYY-MM",
        "is_current": false,
        "achievements": ["Raw verbatim bullet 1", "Raw verbatim bullet 2"]
    }}],
    // EXAMPLE INPUT -> OUTPUT:
    // Input: "Identity: Born 15.05.1988 | Nationality: German" -> "nationality": "German", "date_of_birth": "1988-05-15"
    // Input: "Address: Musterstraße 14, 10117 Berlin, Germany" -> "street_address": "Musterstraße 14", "postal_code": "10117", "city": "Berlin", "country": "Germany"
    "projects": [{{
        "title": "Project Name",
        "role": "Role (or 'Developer')",
        "start_date": "YYYY-MM",
        "end_date": "YYYY-MM",
        "is_current": false,
        "description": "Raw verbatim description (Do not summarize)",
        "link": "https://project-url.com",
        "technologies": ["Tech1", "Tech2"]
    }}],
    "education": [{{
        "degree": "Degree",
        "institution": "University",
        "city": "City",
        "country": "Country",
        "location": "Full Location String",
        "graduation_date": "YYYY-MM",
        "gpa": "Score"
    }}]
}}

Resume Text:
{safe_text}
"""
        
        # Use a more reliable model specifically for high-stakes extraction
        # Forces Gemini 2.0 Flash or 1.5 Flash if available, avoids buggy free auto-routing
        extraction_model = "google/gemini-2.0-flash-exp:free" 
        
        result = await self._call_openrouter(prompt, temperature=0.1, max_tokens=3000, model_override=extraction_model)
        if not result:
            result = await self._call_api(prompt, temperature=0.1, max_tokens=3000)
            
        if not result:
            return {}
            
        try:
            # Parse and Validate with Pydantic
            try:
                json_str = self._clean_json_string(result)
                validated_data = ExtractionResponse.model_validate_json(json_str)
                data = validated_data.model_dump()
            except ValidationError as ve:
                logger.error(f"Extraction Schema Validation Failed: {ve}")
                return {} # Fallback to empty
            except Exception as e:
                logger.error(f"Failed to parse extraction JSON: {str(e)}")
                return {}
            
            # Post-Processing: Fix Null Dates using Header Heuristics
            # If AI returns null dates for projects, try to find a "Section Date" in the raw text
            import re
            section_date = None
            # Look for "PROJECTS... 2023 - Current" pattern
            date_header_match = re.search(r'PROJECTS.*?(\d{4})\s*-\s*(?:Current|Present|Now)', safe_text, re.IGNORECASE | re.DOTALL)
            if date_header_match:
                section_date = f"{date_header_match.group(1)}-01"  # "2023" -> "2023-01"

            if section_date:
                for proj in data.get("projects", []):
                    if not proj.get("start_date"):
                        proj["start_date"] = section_date
                        proj["is_current"] = True # Assume section header "Current" applies
            
            # Post-Processing: Fix Experience Parsing (Company vs Title)
            # Heuristic: If Title is ALL CAPS (likely a company) and Company is empty, SWAP them.
            for exp in data.get("work_experiences", []):
                jt = (exp.get("job_title") or "").strip()
                comp = (exp.get("company") or "").strip()
                
                # Check for BROTOTYPE specifically or ALL CAPS heuristic
                if (not comp) and jt:
                    if jt.upper() == "BROTOTYPE" or (jt.isupper() and len(jt) > 3):
                        exp["company"] = jt
                        exp["job_title"] = "Professional" # Default title
            
            # Post-Processing: Map Language Levels to A1-C2 Scale
            if "languages" in data:
                for lang in data["languages"]:
                    if isinstance(lang, dict) and "level" in lang:
                        lang["level"] = self._map_language_level(lang["level"])

            return data
        except Exception as e:
            logger.error(f"Failed to parse extraction JSON: {str(e)}")
            return {}

    def _map_language_level(self, level: str) -> str:
        """Map descriptive proficiency terms to A1-C2 scale for German compliance"""
        if not level: return "B1"
        l = level.lower().strip()
        
        # C2 - Mastery / Native
        if any(x in l for x in ["native", "muttersprache", "mastery", "expert", "bilingual", "c2"]):
            return "C2"
        # C1 - Advanced / Fluent
        if any(x in l for x in ["fluent", "flie\u00dfend", "advanced", "c1", "highly proficient"]):
            return "C1"
        # B2 - Upper Intermediate / Professional
        if any(x in l for x in ["proficient", "professional", "vibrant", "b2", "good"]):
            return "B2"
        # B1 - Intermediate / Working knowledge
        if any(x in l for x in ["intermediate", "working", "b1", "solid"]):
            return "B1"
        # A2 - Elementary
        if any(x in l for x in ["elementary", "basic", "a2", "limited"]):
            return "A2"
        # A1 - Beginner
        if any(x in l for x in ["beginner", "a1", "starter", "introduction"]):
            return "A1"
            
        return level # Return original if no match

    async def generate_resume_title(self, user_data: Dict[str, Any], job_description: str = "") -> str:
        """Generate a catchy title for the resume based on role/JD"""
        role = user_data.get("headline", "Professional")
        prompt = f"Given a candidate's profile ({role}) and job description ({job_description[:500] if job_description else 'N/A'}), suggest a concise 3-5 word title for this resume. Return ONLY the title text."
        
        result = await self._call_api(prompt, temperature=0.7, max_tokens=20)
        return result or f"{role} Resume"

    def _analyze_and_tag_experiences(self, experiences: List[Dict], target_scope: str) -> str:
        """
        Analyze experiences and generate specific Reframing Instructions for the AI.
        Used to bridge the gap between 'Web Dev' history and 'AI Engineer' target.
        """
        instructions = []
        target_keywords = DOMAIN_SIGNALS.get(target_scope, [])
        if not target_keywords:
            return ""

        for exp in experiences:
            role = exp.get("job_title", "Unknown Role")
            company = exp.get("company", "Unknown Company")
            raw_desc = exp.get("description", [])
            desc = (" ".join(raw_desc) if isinstance(raw_desc, list) else str(raw_desc)).lower()
            
            # Determine domain of this specific experience
            exp_scope = self._classify_job_scope(role)
            
            # If Scope Mismatch (e.g. Web Dev -> AI), generate instruction
            if exp_scope != target_scope and target_scope == "AI / ML":
                instructions.append(f"""
        - ROLE: "{role}" at "{company}" (CONTEXT: {exp_scope})
          -> ACTION: IGNORE generic web/frontend details (HTML, CSS, WP).
          -> REFRAME AS: "AI-Ready Infrastructure" or "Data/Backend Automation".
          -> STRATEGY: Find any data flow, API, or optimization work and rewrite it as a foundation for AI systems.
                """.strip())
            
            elif exp_scope == "Frontend" and target_scope == "Backend":
                 instructions.append(f"""
        - ROLE: "{role}" at "{company}"
          -> ACTION: Focus ONLY on API integration, state management, and performance. Minimize UI/Design details.
                 """.strip())

        if instructions:
            return "\nSPECIFIC REFRAMING STRATEGY (APPLY RUTHLESSLY):\n" + "\n".join(instructions)
        return ""

    def _analyze_career_trajectory(self, experiences: List[Dict], target_role: str) -> str:
        """
        Identify the career arc (Pivot, Consistency, Specialization).
        Returns a 'Narrative Strategy' to guide the summary writer.
        """
        if not experiences:
            return "Narrative Strategy: Highlight potential and eagerness to learn."
            
        # 1. Classify Origin (Oldest/First Role)
        # Assuming sorted? If not, pick the last one in list (usually oldest if reverse chrono)
        # But safest is to just check the "Dominant Past"
        past_roles = [e.get("job_title", "") for e in experiences]
        past_text = " ".join(past_roles)
        origin_scope = self._classify_job_scope(past_text) # Crude mix
        
        target_scope = self._classify_job_scope(target_role)
        
        # 2. Determine Arc
        msg = ""
        if origin_scope == target_scope:
            msg = f"NARRATIVE ARC: CONSISTENCY. The candidate has a strong track record in {target_scope}. Emphasize 'Deep Expertise', 'Seniority', and 'Proven Success'."
        elif origin_scope == "Frontend" and target_scope == "Full-Stack":
            msg = "NARRATIVE ARC: GROWTH. Transitioning from Frontend to Full-Stack. Emphasize 'Expanded Scope' and 'End-to-End Delivery'."
        elif origin_scope == "Backend" and target_scope == "AI / ML":
            msg = "NARRATIVE ARC: EVOLUTION (Backend -> AI). Position as a 'Systems Engineer applying rigorous backend principles to AI'. Emphasize 'Scalability' and 'Production-Grade AI'."
        elif "Web" in origin_scope and target_scope == "AI / ML":
            if any(x in target_role.lower() for x in ["trainee", "intern", "junior", "student"]):
                 msg = "NARRATIVE ARC: EVOLUTION (Web -> AI). Position as a 'Frontline Developer with AI-readiness'. Emphasize 'Deployment' and 'API Integration' skills."
            else:
                 msg = "NARRATIVE ARC: SYSTEMS PIVOT (Web -> AI). Sound like a 'Senior Web Specialist pivoting to AI'. Value Prop: 'I know how to deploy AI apps, not just train models'."
        elif origin_scope != target_scope:
            msg = f"NARRATIVE ARC: STRATEGIC PIVOT ({origin_scope} -> {target_scope}). Emphasize transferable skills (e.g., Problem Solving, Engineering Discipline) found in the past roles that apply to the new target."
        
        return msg

    def _generate_transformation_audit(self, original_data: Dict, spun_data: Dict, job_title: str):
        """
        Compare Original vs Spun data and save a human-readable diff log.
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = "logs/transformations"
            os.makedirs(log_dir, exist_ok=True)
            filename = f"{log_dir}/audit_{timestamp}.txt"
            
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"=== TRANSFORMATION AUDIT [{timestamp}] ===\n")
                f.write(f"Target Role: {job_title}\n\n")
                
                # 1. Summary Audit
                f.write("--- PROFESSIONAL SUMMARY ---\n")
                # Need to find original summary (might be deleted in cleaned_data, fetch from original_data passed in)
                orig_sum = original_data.get("professional_summary") or original_data.get("summary") or "[No Original Summary]"
                new_sum = spun_data.get("professional_summary", "[No New Summary]")
                
                f.write(f"ORIGINAL:\n{orig_sum}\n\n")
                f.write(f"AI SPUN:\n{new_sum}\n")
                if "Full-Stack" in new_sum or "Web Developer" in new_sum:
                     f.write("\n[WARNING]: Identity Leak Detected in Spun Summary!\n")
                else:
                     f.write("\n[PASS]: Identity Lock Active.\n")
                f.write("-" * 40 + "\n\n")
                
                # 2. Experience Audit
                f.write("--- EXPERIENCE REFRAMING ---\n")
                orig_exps = original_data.get("work_experiences", [])
                new_exps = spun_data.get("work_experiences", [])
                
                # Map by ID if possible, else index
                for i, new_exp in enumerate(new_exps):
                    comp = new_exp.get("company", "Unknown")
                    role = new_exp.get("job_title", "Unknown")
                    f.write(f"ROLE: {role} at {comp}\n")
                    
                    # Find matching original
                    orig_exp = next((e for e in orig_exps if e.get("company") == comp), None)
                    if orig_exp:
                        orig_bullets = orig_exp.get("description", [])
                        new_bullets = new_exp.get("description", [])
                        
                        for j, nb in enumerate(new_bullets):
                            ob = orig_bullets[j] if j < len(orig_bullets) else "[New Bullet]"
                            f.write(f"  - ORIG: {ob[:100]}...\n")
                            f.write(f"  + SPUN: {nb}\n")
                    else:
                        f.write("  [New Role Created or Unmatched]\n")
                    f.write("\n")
                    
            logger.info(f"Transformation audit saved to: {filename}")
            return filename
        except Exception as e:
            logger.error(f"Failed to save transformation audit: {e}")
            return None

    def _validate_country_compliance(self, user_data: Dict, knowledge_base: Dict) -> List[str]:
        """
        Validate user data against country-specific RAG rules.
        Returns a list of warnings (strings) with market-specific feedback.
        """
        warnings = []
        country = knowledge_base.get("country", "").lower()
        
        # 1. Base Checks (Universal)
        contact = user_data.get("contact", {})
        if not contact.get("email"):
            warnings.append("Missing Email Address (Critical).")
        if not contact.get("phone"):
            warnings.append("Missing Phone Number.")
            
        # 2. Extract RAG Rules
        cv_struct = knowledge_base.get("cv_structure", {})
        mandatory_config = cv_struct.get("mandatory_sections", {})
        cultural = knowledge_base.get("cultural_rules", {})
        formatting = knowledge_base.get("formatting", {})
        
        # 3. Photo Check (Cultural)
        photo_rule = cultural.get("photo", "").lower()
        has_photo = bool(user_data.get("profile_pic_url"))
        if ("common" in photo_rule or "required" in photo_rule) and not has_photo:
            warnings.append(f"Photo Recommendation: {cultural.get('photo')} (Missing from profile).")
        
        # 4. Mandatory Section Presence Check
        # Maps user_data keys to RAG mandatory section names
        section_map = {
            "work_experiences": ["Work Experience", "職歴"],
            "educations": ["Education", "学歴"],
            "skills": ["Skills", "スキル"],
            "certifications": ["Qualifications & Licenses", "資格・免許"],
            "projects": ["Projects"]
        }
        
        rag_mandatory = cv_struct.get("order", [])
        for section_name in rag_mandatory:
            # Check if this mandatory section is missing in user data
            is_missing = True
            for data_key, aliases in section_map.items():
                if any(alias in section_name for alias in aliases):
                    if user_data.get(data_key):
                        is_missing = False
                    break
            
            # Special logic for summary/self-pr/motivation which are often synthesized
            if "Self-PR" in section_name or "Motivation" in section_name:
                if not user_data.get("professional_summary") and not user_data.get("summary"):
                    is_missing = True
                else:
                    is_missing = False

            if is_missing:
                clean_name = re.sub(r'\(.*\)', '', section_name).strip()
                warnings.append(f"Missing Section: {clean_name} is highly recommended for {country.title()}.")

        # 5. Japan-Specific Strict Logic
        if "japan" in country:
            # Pronoun Check (Strict)
            summary = user_data.get("professional_summary", "") or user_data.get("summary", "")
            if re.search(r'\b(I|me|my|mine|we|our|ours)\b', summary, re.IGNORECASE):
                warnings.append("Style Warning (Japan): Avoid first-person pronouns (I, my, we) in Self-PR.")
            
            # Language Mapping Suggestions
            languages = user_data.get("languages", [])
            for lang in languages:
                name = lang.get("name", "").lower()
                level = lang.get("level", "").upper()
                if "japanese" in name:
                    if level in ["C1", "C2", "NATIVE"]:
                        pass # Excellent
                    elif level in ["B2", "INTERMEDIATE"]:
                        warnings.append("Japanese Level: Map to JLPT N2 for better professional recognition.")
                    elif level in ["B1", "A2"]:
                        warnings.append("Japanese Level: Map to JLPT N3/N4 as per market standards.")
                elif "english" in name and level in ["C1", "C2", "B2"]:
                     if not any("toeic" in str(l).lower() for l in languages):
                         warnings.append("English Certification: Consider adding a TOEIC score (800+) for Japanese firms.")

        # 6. Address/Location
        personal_reqs = mandatory_config.get("personal_info", {}).get("required", [])
        if "City" in personal_reqs or "Location" in personal_reqs or "Address" in personal_reqs:
            if not contact.get("city") and not contact.get("location") and not user_data.get("street_address"):
                warnings.append(f"Missing Address: Required for {country.title()} compliance.")

        return warnings

    async def generate_resume_text(self, user_data: Dict[str, Any], country: str, language: str, template_style: str, job_description: str = "") -> str:
        """Generate full resume content as HTML fragments"""
        logger.warning("generate_resume_text is deprecated. Routing to generate_tailored_resume.")
        prompt = f"""
You are an expert resume writer specializing in the {country} job market.
Your task is to generate a professional, high-impact resume in {language} for the candidate provided.

TARGET MARKET: {country}
LANGUAGE: {language}
STYLE: {template_style}

CANDIDATE DATA:
{json.dumps(user_data, indent=2, ensure_ascii=False)}

JOB DESCRIPTION / TARGET ROLE:
{job_description if job_description else "Professional standard for the candidate's current headline."}

INSTRUCTIONS:
1. Rewrite the professional summary and achievements to align with the job description (if provided).
2. Emphasize keywords and skills relevant to the target role.
3. Use ONLY valid HTML fragments (div, h2, ul, li, p, span). Do NOT include <html> or <body> tags.
4. Structure the content using these specific classes for compatibility:
   - <div class="section"><h2 class="section-title">SECTION NAME</h2> ... content ... </div>
   - Use <ul> and <li> for achievements and skills.
   - For job headers, use:
     <div class="job-header">
       <span class="job-title">Job Title</span> - <span class="job-company">Company</span>
       <span class="job-date">Date Range</span>
     </div>

Return a comprehensive resume that fills about one page of content.
"""
        result = await self._call_api(prompt, temperature=0.7, max_tokens=3000)
        return result or "AI Content Generation temporarily unavailable."

    async def generate_tailored_resume(self, user_data: Dict[str, Any], job_description: str, country: str, language: str, job_title: str, ats_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Generate a strictly tailored resume using 'The Strategist' logic.
        1. GHOSTING: Removes original summary to force fresh generation.
        2. SPIN: Rewrites experiences to bridge gaps to the JD.
        3. RAG: Enforces strict country rules (Dates, Headers, Verbs).
        """
        try:
            # 1. RAG Context Loading
            knowledge_base = {}
            date_format = "MM/YYYY"
            try:
                from services.rag_service import RAGService
                rag_data = RAGService.get_complete_rag(country, language)
                knowledge_base = rag_data.get("knowledge_base", {})
                language_template = rag_data.get("language_template", {})
                date_format = language_template.get("date_format", "DD.MM.YYYY")
            except Exception as e:
                logger.warning(f"RAG Load Failed: {e}. Using fallbacks.")

            # 2. Preparation & Ghosting (Anti-Parrot)
            cleaned_data = copy.deepcopy(user_data)
            for field in ["professional_summary", "summary", "bio", "about", "headline"]:
                cleaned_data.pop(field, None)
            
            # Date Consistency
            for exp in cleaned_data.get("work_experiences", []):
                if exp.get("is_current") or not exp.get("end_date"):
                    exp["end_date"] = "Present"

            # 3. Prompt Construction
            target_scope = self._classify_job_scope(job_title)
            reframing_instructions = self._analyze_and_tag_experiences(cleaned_data.get("work_experiences", []), target_scope)
            arc_strategy = self._analyze_career_trajectory(cleaned_data.get("work_experiences", []), job_title)
            
            dob_lock = user_data.get('date_of_birth', '')
            nat_lock = user_data.get('nationality', '')

            prompt = f"""
            ROLE: Senior Career Strategist ({country})
            OBJECTIVE: Rewrite this profile for the target role: {job_title}.
            
            STRICT RULES:
            - NO PRONOUNS (I, me, my).
            - DATE FORMAT: {date_format}
            - IDENTITY LOCK: Born: {dob_lock}, Nationality: {nat_lock}
            
            {arc_strategy}
            {reframing_instructions}
            
            INPUT DATA: 
            {json.dumps(cleaned_data, ensure_ascii=False)}
            
            JD: 
            {job_description[:1000]}
            
            OUTPUT: Valid JSON with keys: professional_summary, headline, work_experiences, educations, projects, skills, languages.
            """

            # 4. AI Call
            response = await self._call_api_rich(prompt, temperature=0.4, max_tokens=2500)
            result_text = response.get("content")
            
            if not result_text:
                return {"success": False, "error": "AI_EMPTY_RESPONSE"}

            # 5. Parsing & Pydantic Validation
            try:
                json_str = self._clean_json_string(result_text)
                validated_data = TailoredResumeResponse.model_validate_json(json_str)
                generated_content = validated_data.model_dump()
                generated_content = self._sanitize_spun_data(generated_content)
            except ValidationError as ve:
                logger.error(f"Tailored Resume Schema Validation Failed: {ve}")
                return {"success": False, "error": "AI_SCHEMA_VALIDATION_ERROR"}
            except Exception as e:
                logger.error(f"JSON Parse Error: {e}")
                return {"success": False, "error": "AI_JSON_ERROR"}

            # Update local data
            for key in ["professional_summary", "headline", "work_experiences", "educations", "projects", "skills", "languages"]:
                if key in generated_content:
                    cleaned_data[key] = generated_content[key]

            # 6. Global Fixes (Identity Lock & Formatting)
            try:
                # Ghost Protocol (Identity Protection)
                cleaned_data["professional_summary"] = self._apply_ghost_protocol(cleaned_data.get("professional_summary", ""), job_title)
                
                # Country Compliance
                if country.lower() in ["germany", "japan"]:
                    if user_data.get("date_of_birth"):
                        cleaned_data["date_of_birth"] = self._format_date_by_country(user_data["date_of_birth"], country)
                    cleaned_data["nationality"] = user_data.get("nationality")
                
                # Base64 Photo
                if cleaned_data.get("profile_pic_url"):
                    cleaned_data["profile_pic_base64"] = await self._encode_image_to_base64(cleaned_data["profile_pic_url"])

                # Audit & Compliance
                self._generate_transformation_audit(user_data, cleaned_data, job_title)
                warnings = self._validate_country_compliance(cleaned_data, knowledge_base)

                return {
                    "success": True,
                    "resume_content": generated_content,
                    "generated_summary": cleaned_data["professional_summary"],
                    "spun_data": cleaned_data,
                    "audit_log": {
                        "provider": response.get("provider", "Gemini"),
                        "compliance_warnings": warnings
                    }
                }
            except Exception as post_err:
                logger.warning(f"Non-critical post-processing failure: {post_err}")
                return {
                    "success": True,
                    "resume_content": generated_content,
                    "generated_summary": cleaned_data.get("professional_summary", ""),
                    "spun_data": cleaned_data,
                    "audit_log": {"warn": "partial_success"}
                }

        except Exception as e:
            logger.error(f"Critical Failure in generate_tailored_resume: {e}")
            return {"success": False, "error": "AI_CRITICAL_FAILURE", "details": str(e)}

    def _sanitize_spun_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Strip contact information and links from professional sections."""
        if not data or not isinstance(data, dict): return data
        for list_key in ["skills", "languages"]:
            if list_key in data and isinstance(data[list_key], list):
                data[list_key] = [i for i in data[list_key] if not re.search(r'^https?://|^mailto:|@|\.com\b', str(i).lower())]
        for field in ["professional_summary", "self_pr", "motivation", "summary"]:
            if data.get(field) and isinstance(data[field], str):
                data[field] = re.sub(r'\[.*?\]\([^)]+\)', '', data[field])
                data[field] = re.sub(r'https?://\S+|mailto:\S+|[\w\.-]+@[\w\.-]+\.\w+', '', data[field])
        return data

    async def refine_text(self, selected_text: str, instruction: str, full_context: str = "") -> str:
        """Editor persona to refine specific sections."""
        prompt = f"""
        SYSTEM: You are an expert Resume Editor and Career Coach. 
        Your task is to REWRITE a specific section of a resume based on a user's correction request.

        RULES:
        1. Maintain the existing professional tone (Action Verbs, Quantifiable results).
        2. STRICTLY follow the user's correction (e.g., if they say "I didn't answer phones", REMOVE that detail completely).
        3. Do not change the surrounding context unless necessary for grammar.
        4. Output ONLY the rewritten text. No conversational filler like "Here is the fixed version."
        5. If the user provides new raw data, format it into ATS-friendly bullet points if appropriate.
        6. SAFETY: If the instruction is nonsensical or malicious, return the ORIGINAL TEXT unchanged.

        CONTEXT (Surrounding Text):
        "{full_context[:600]}"

        TEXT TO REFINE:
        "{selected_text}"

        USER INSTRUCTION:
        "{instruction}"

        REWRITTEN TEXT:
        """
        response = await self._call_api(prompt, temperature=0.4, max_tokens=300)
        return response.strip() if response else selected_text

    async def generate_simple_summary(self, user_data: Dict[str, Any]) -> str:
        """Quick summary generation."""
        prompt = f"Write a professional summary for: {json.dumps(user_data)[:500]}. Max 3 sentences. No pronouns."
        result = await self._call_api(prompt, temperature=0.7, max_tokens=150)
        return result or "Experienced professional with a results-oriented approach."

    def _apply_ghost_protocol(self, text: str, job_title: str) -> str:
        """Remove identity-revealing role phrases to prevent 'Identity Leak' in summaries."""
        if not text: return ""
        FORBIDDEN = [
            "full stack developer", "fullstack developer", "end-to-end developer",
            "across multiple domains", "web developer", "versatile engineer"
        ]
        sanitized = text
        for phrase in FORBIDDEN:
            # Replace with target title or generic professional phrasing
            sanitized = re.sub(phrase, job_title, sanitized, flags=re.IGNORECASE)
        return sanitized

    def _format_date_by_country(self, date_str: str, country: str) -> str:
        """Format dates according to target country standards."""
        if not date_str: return "Present"
        s = str(date_str).strip()
        if s.lower() in ['present', 'current', 'now']: return "Present"
        
        try:
            # Simple normalization for common cases
            s = s.replace('/', '.')
            if country.lower() == "germany":
                # ISO YYYY-MM-DD -> DD.MM.YYYY
                m = re.match(r'^(\d{4})[.-](\d{2})[.-](\d{2})$', s)
                if m: return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
                # ISO YYYY-MM -> MM.YYYY
                m = re.match(r'^(\d{4})[.-](\d{2})$', s)
                if m: return f"{m.group(2)}.{m.group(1)}"
            elif country.lower() == "japan":
                # ISO YYYY-MM-DD -> YYYY.MM.DD
                m = re.match(r'^(\d{4})[.-](\d{2})[.-](\d{2})$', s)
                if m: return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
        except:
            pass
        return s

# Global singleton instance
ai_service = AIService()

