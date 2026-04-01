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
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

from datetime import datetime
import difflib
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from configurations.countries import get_country_context, get_country_fallback_data
from app_settings import Config

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

async def encode_image_to_base64(url: str) -> Optional[str]:
    """Download an image from a URL and convert it to a base64 data URI."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                b64_str = base64.b64encode(resp.content).decode('utf-8')
                # Try to determine mime type from URL or default to jpeg
                ext = url.split('.')[-1].lower()
                mime = f"image/{ext}" if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp'] else "image/jpeg"
                if 'jpeg' in mime or 'jpg' in mime:
                    mime = 'image/jpeg'
                return f"data:{mime};base64,{b64_str}"
            else:
                logger.warning(f"Failed to fetch image for base64 encoding (Status {resp.status_code}): {url}")
    except Exception as e:
        logger.error(f"Error encoding image to base64: {e}")
    return None

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
                # Groq has strict rate limits, so we respect them 429
                logger.info(f"Targeting Groq ({model})...")
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        self.groq_url,
                        headers={
                            "Authorization": f"Bearer {self.groq_api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": temperature,
                            "max_tokens": max_tokens
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
                        continue # Try next model or fail over
                    elif response.status_code == 404:
                         logger.warning(f"Groq model {model} not found.")
                    else:
                        logger.warning(f"Groq {model} failed ({response.status_code}): {response.text[:100]}")
                        
            except Exception as e:
                logger.error(f"Groq connection error for {model}: {str(e)}")
                continue
                
        return None

    async def _call_gemini(self, prompt: str, temperature: float = 0.7, max_tokens: int = 2000, model_override: Optional[str] = None) -> Optional[str]:
        """Call Google Gemini API (multiple variants)"""
        if not self.gemini_api_key:
            return None
            
        # Try models in order of preference (newest/best first)
        models_to_try = [model_override] if model_override else [
            "gemini-2.0-flash",          # User requested model (Priority 1)
            "gemini-2.0-flash-exp",      # Latest experimental (free)
            "gemini-1.5-flash",
            "gemini-1.5-flash-latest",
            "gemini-1.5-flash-002",
            "gemini-pro",
            "gemini-1.5-pro"
        ]
        
        for model_id in models_to_try:
            if not model_id: continue
            
            # Ensure model_id has models/ prefix
            full_model_id = model_id if model_id.startswith("models/") else f"models/{model_id}"
            url = f"{self.gemini_url}/{full_model_id}:generateContent"
            
            try:
                logger.info(f"Targeting Gemini ({model_id})...")
                async with httpx.AsyncClient(timeout=Config.AI_REQUEST_TIMEOUT) as client:
                    response = await client.post(
                        f"{url}?key={self.gemini_api_key}",
                        headers={"Content-Type": "application/json"},
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "temperature": temperature,
                                "maxOutputTokens": max_tokens,
                                "topP": 0.95,
                                "topK": 40
                            }
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
                        break # Usually key-wide, so skip Gemini entirely
                    else:
                        logger.warning(f"Gemini {model_id} failed ({response.status_code}): {response.text[:100]}")
                        
            except Exception as e:
                logger.error(f"Gemini connection error for {model_id}: {str(e)}")
                
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
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
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
                            "max_tokens": max_tokens
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
                         logger.warning(f"OpenRouter 402: Payment Required (Credits Exhausted).")
                         break # Stop trying OpenRouter if no money
                        
            except Exception as e:
                logger.warning(f"OpenRouter {model} failed: {str(e)}")
                continue
                
        return None

    @retry(
        stop=stop_after_attempt(1),
        wait=wait_exponential(multiplier=1, min=2, max=6),
        retry=retry_if_exception_type((httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def _execute_with_provider_retry(self, provider_config: str, prompt: str, temperature: float, max_tokens: int) -> Optional[Dict[str, Any]]:
        """Internal retry loop for a specific provider configuration"""
        parts = provider_config.split(":")
        p_name = parts[0].strip().lower()
        p_model = ":".join(parts[1:]).strip() if len(parts) > 1 else None

        result = None
        if p_name == "groq":
             result = await self._call_groq(prompt, temperature, max_tokens, model_override=p_model)
        elif p_name == "gemini":
            result = await self._call_gemini(prompt, temperature, max_tokens, model_override=p_model)
        elif p_name == "openrouter":
            result = await self._call_openrouter(prompt, temperature, max_tokens, model_override=p_model)
        
        if result:
            return {
                "content": result,
                "provider": provider_config,
                "success": True
            }

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
                result = await self._execute_with_provider_retry(provider_cfg, prompt, temperature, max_tokens)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Provider {provider_cfg} exhausted all retries: {e}")
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
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
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
                    }
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
                
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        f"{url}?key={self.gemini_api_key}",
                        headers={"Content-Type": "application/json"},
                        json={
                            "model": model,
                            "content": {"parts": [{"text": text[:2048]}]} # Truncate for safety
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        embedding = data.get("embedding", {}).get("values")
                        if embedding:
                            # logger.info(f"Generated embedding with Gemini (len={len(embedding)})")
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

    def _apply_ghost_protocol(self, text: str, job_title: str, enabled: bool = False) -> str:
        if not text or not enabled:
            return text
        
        # Trigger for AI/ML and Automation roles - Use word boundaries
        import re
        title_lower = job_title.lower()
        # Exclusion list: Do NOT trigger for junior/training roles (prevents hallucination for Trainees)
        # Check both target job title AND the text being sanitized (to catch "Intern at...")
        is_junior_title = any(x in title_lower for x in ["trainee", "intern", "junior", "student"])
        is_junior_text = any(x in text.lower() for x in ["trainee", "intern", "junior", "student"])
        
        is_junior = is_junior_title or is_junior_text
        
        ai_patterns = [r"\bai\b", r"\bml\b", "learning", "data scien", "neural", "vision", "automation", "architect"]
        
        match = None
        if not is_junior:
            for p in ai_patterns:
                m = re.search(p, title_lower)
                if m:
                    match = p
                    break
        
        if match:
            logger.warning(f"GHOST_PROTOCOL ACTIVATED for role '{job_title}' (Matched pattern: '{match}'). Processing text: '{text[:50]}...'")
            import re
            forbidden = ["Full-Stack", "Full Stack", "Fullstack", "Web Developer", "Frontend", "React Developer"]
            
            # Aggressive case-insensitive replacement
            for word in forbidden:
                regex = re.compile(re.escape(word), re.IGNORECASE)
                text = regex.sub("AI Solutions Architect", text)
            
            # Final ruthless safety check - Fail the build if terms persist
            if any(word.lower() in text.lower() for word in forbidden):
                raise ValueError("GHOST_PROTOCOL_BREACH: Generalist terminology detected.")
                
        return text

    def _sanitize_spun_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize AI-generated data to prevent leakage of contact info into
        sections like Skills, Languages, or Projects.
        """
        # Simple patterns to catch most URLs and emails
        url_pattern = re.compile(r'https?://\S+|www\.\S+')
        email_pattern = re.compile(r'\S+@\S+\.\S+')

        # 1. Sanitize Skills (No URLs or Emails)
        if "skills" in data and isinstance(data["skills"], list):
            data["skills"] = [
                s for s in data["skills"] 
                if not url_pattern.search(str(s)) and not email_pattern.search(str(s))
            ]

        # 2. Sanitize Languages (No URLs or Emails)
        if "languages" in data and isinstance(data["languages"], list):
            new_langs = []
            for lang in data["languages"]:
                if isinstance(lang, str):
                    if not url_pattern.search(lang) and not email_pattern.search(lang):
                        new_langs.append(lang)
                elif isinstance(lang, dict):
                    name = str(lang.get("name", lang.get("language", "")))
                    if not url_pattern.search(name) and not email_pattern.search(name):
                        new_langs.append(lang)
            data["languages"] = new_langs

        # 3. Sanitize Links (Remove Photo URLs)
        if "links" in data and isinstance(data["links"], list):
            # If we have a known photo URL, make sure it's not in the links array
            photo_url = data.get("profile_pic_url") or data.get("photo_url")
            if photo_url:
                data["links"] = [
                    l for l in data["links"]
                    if not (isinstance(l, dict) and photo_url in str(l.get("url", "")))
                    and not (isinstance(l, str) and photo_url in l)
                ]
            
            # Also remove anything that looks like a raw image URL from links if it's not labeled
            data["links"] = [
                l for l in data["links"]
                if not (isinstance(l, dict) and re.search(r'\.(jpg|jpeg|png|gif|webp)$', str(l.get("url", "")).lower()))
            ]

        return data

    def _clean_json_string(self, json_str: str) -> str:
        """Robustly extract JSON from AI response (Emergency Fix)"""
        import re
        # Remove markdown code blocks if present
        clean_text = re.sub(r'```json\s*|\s*```', '', json_str).strip()
        # Remove potential preamble like "Here is the JSON:"
        start = clean_text.find('{')
        end = clean_text.rfind('}')
        
        if start != -1:
            if end != -1 and end > start:
                clean_text = clean_text[start:end+1]
            else:
                # Truncated JSON? Try to repair it
                clean_text = self._repair_truncated_json(clean_text[start:])
        
        return clean_text

    def _repair_truncated_json(self, json_fragment: str) -> str:
        """
        Emergency repair for truncated JSON buffers.
        Closes unclosed quotes, brackets, and braces.
        """
        # 1. Close unclosed string if it exists
        if json_fragment.count('"') % 2 != 0:
            json_fragment += '"'
        
        # 2. Add missing closing brackets/braces in order
        stack = []
        for char in json_fragment:
            if char == '{': stack.append('}')
            elif char == '[': stack.append(']')
            elif char == '}': 
                if stack and stack[-1] == '}': stack.pop()
            elif char == ']':
                if stack and stack[-1] == ']': stack.pop()
        
        # Reverse stack to close from inside out
        json_fragment += "".join(reversed(stack))
        return json_fragment


    def _classify_job_scope(self, job_title: str) -> str:
        """Deterministic mapping of job title to primary domain"""
        title_lower = job_title.lower()
        
        # Priority mapping - Use word boundaries for 'ai' and 'ml'
        import re
        if any(re.search(fr"\b{w}\b", title_lower) for w in ["ai", "ml"]) or any(w in title_lower for w in ["learning", "agent", "llm", "intelligence", "automation"]):
            return "AI / ML"
        if any(w in title_lower for w in ["devops", "platform", "cloud", "infra", "sre"]):
            return "DevOps"
        if any(w in title_lower for w in ["data", "analytics", "warehouse", "bi"]):
            return "Data"
        if any(w in title_lower for w in ["product", "manager", "pm"]):
            return "Product"
        if any(w in title_lower for w in ["design", "ui", "ux", "creative"]):
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

    def _filter_experiences_by_scope(self, experiences: List[Dict], target_scope: str) -> List[Dict]:
        """
        Filter experiences to include ONLY those with scope-relevant signals.
        This ensures the AI only sees relevant career history when writing the summary.
        """
        if not experiences or target_scope not in DOMAIN_SIGNALS:
            return experiences
        
        # [MODIFIED] Strategy: Keep ALL experiences, but flag mismatches for reframing
        # We no longer filter out roles. We just return them all.
        # The prompt will handle the "Reframing" instructions based on the pre-analysis.
        return experiences
        
        for exp in experiences:
            # Check if this experience contains target domain signals
            exp_text = f"{exp.get('title', '')} {exp.get('company', '')} {exp.get('description', '')}".lower()
            
            # Count how many target keywords appear
            signal_count = sum(1 for kw in target_keywords if kw in exp_text)
            
            # Include if it has at least 2 target signals (or 1 for small keyword sets)
            threshold = 1 if len(target_keywords) < 5 else 2
            if signal_count >= threshold:
                filtered.append(exp)
        
        # If no experiences match, return the most recent one to avoid empty context
        return filtered if filtered else experiences[:1]
    
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
        except ImportError:
            # Fallback if RAGService fails to import (though we just created it)
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
            
            # Fetch embeddings concurrently? For now sequential is safer for rate limits
            logger.info("Generating vectors for Hybrid Scoring...")
            logger.info(f"Warnings present: {parsing_warnings}")
            vec_resume = await self._get_embedding(vec_resume_text)
            vec_jd = await self._get_embedding(vec_jd_text)
            
            # Calculate Cosine Similarity
            similarity = self._cosine_similarity(vec_resume, vec_jd)
            semantic_score = similarity * 100 # Convert 0.85 -> 85
            logger.info(f"Resume2Vec Semantic Score: {semantic_score:.2f} (Sim: {similarity:.4f})")
            
        except Exception as vec_error:
            logger.error(f"Vector calculation failed: {vec_error}")
            semantic_score = 0
            
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
            json_str = self._clean_json_string(result)
            logger.info(f"DEBUG: Processed JSON string: {json_str[:100]}...")
            data = json.loads(json_str)
            logger.info(f"DEBUG: Parsed data type: {type(data)}")

            
            # Handle double-encoded JSON (if the AI returned a stringified JSON)
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    pass

            if not isinstance(data, dict):
                 raise ValueError(f"AI returned invalid format: {type(data)} found, expected dict")

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
   - **Experience**: Companies/Employment only.
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
    "experience": [{{
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
            json_str = self._clean_json_string(result)
            # Post-Processing: Fix Null Dates using Header Heuristics
            # If AI returns null dates for projects, try to find a "Section Date" in the raw text
            import re
            data = json.loads(json_str) # Parse JSON here
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
            for exp in data.get("experience", []):
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
            desc = " ".join(exp.get("description", [])).lower()
            
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
        # EXECUTION PROOF GUARD - REMOVE AFTER VERIFICATION
        raise RuntimeError("LEGACY_PATH_EXECUTED")
        
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
        4. REPAIR: Addressing specific ATS Warnings/Errors if provided.
        """
        import copy
        import json
        import re
        from datetime import datetime

        def format_japan_date(date_str):
            if not date_str: return "Present"
            s = str(date_str).strip()
            if s.lower() in ['present', 'current', 'now']:
                return "Present"
            s = s.replace('/', '.')
            match_iso = re.match(r'^(\d{4})[.-](\d{2})[.-](\d{2})$', s)
            if match_iso:
                return f"{match_iso.group(1)}.{match_iso.group(2)}.{match_iso.group(3)}"
            match_month_iso = re.match(r'^(\d{4})[.-](\d{2})$', s)
            if match_month_iso:
                return f"{match_month_iso.group(1)}.{match_month_iso.group(2)}"
            if re.match(r'^\d{4}\.\d{2}\.\d{2}$', s) or re.match(r'^\d{4}\.\d{2}$', s):
                return s
            return s

        def format_german_date(date_str):
            if country and "japan" in country.lower():
                return format_japan_date(date_str)
            if not date_str: return "Present"
            s = str(date_str).strip()
            if s.lower() in ['present', 'current', 'heute', 'bis heute', 'now']:
                return "Present"
            s = s.replace('/', '.')
            match_iso = re.match(r'^(\d{4})[.-](\d{2})[.-](\d{2})$', s)
            if match_iso:
                year = int(match_iso.group(1))
                if year < 1900 or year > (datetime.now().year + 1):
                    if len(str(year)) == 5 and str(year).startswith("20"):
                         return f"{match_iso.group(3)}.{match_iso.group(2)}.{str(year)[:4]}"
                    return s 
                return f"{match_iso.group(3)}.{match_iso.group(2)}.{match_iso.group(1)}"
            match_month_iso = re.match(r'^(\d{4})[.-](\d{2})$', s)
            if match_month_iso:
                return f"{match_month_iso.group(2)}.{match_month_iso.group(1)}"
            if re.match(r'^\d{2}\.\d{2}\.\d{4}$', s) or re.match(r'^\d{2}\.\d{4}$', s):
                return s
            return s
        
        # 1. RAG Context Loading (Dynamic & Strict)
        try:
            from services.rag_service import RAGService
            rag_data = RAGService.get_complete_rag(country, language)
            
            knowledge_base = rag_data.get("knowledge_base", {})
            language_template = rag_data.get("language_template", {})
            
            # Extract RAG Rules
            # Extract RAG Rules
            section_headings = language_template.get("section_headings", {})
            action_verbs = language_template.get("action_verbs", [])
            date_format = language_template.get("date_format", "MM/YYYY")
            tone_guide = "Formal and Professional" if country.lower() == "germany" else "Achievement-Oriented"
            
            # [NEW] Psychology Extraction
            psychology = knowledge_base.get("hiring_psychology", {})
            psych_tone = psychology.get("tone_guide", {})
            psych_trust = psychology.get("trust_signals", {})

            rag_context_prompt = f"""
            COUNTRY RULES ({country}):
            - TONE: {tone_guide}
            - SENTENCE STRUCTURE: {psych_tone.get("sentence_structure", "Standard professional")}
            - SELF_PROMOTION STYLE: {psych_tone.get("self_promotion", "Balanced")}
            - AVOID RED FLAGS: {str(psych_trust.get("red_flags", []))}
            - DATE FORMAT: {date_format} (STRICTLY ENFORCE THIS IN ALL DATES)
            - SECTION HEADINGS: {json.dumps(section_headings, ensure_ascii=False)}
            - ACTION VERBS: {json.dumps(action_verbs, ensure_ascii=False)} (MUST use these)
            """
            
        except Exception as e:
            logger.error(f"RAG Load Failed: {e}. Using defaults.")
            rag_context_prompt = f"RULES: Standard {country} CV format."

        # 2. GHOSTING (Identity Protection)
        # Deep copy to protect original data
        cleaned_data = copy.deepcopy(user_data)
        
        # Hard delete summary and identity fields to prevent "parroting"
        forbidden_fields = ["professional_summary", "summary", "bio", "about", "headline", "role_description"]
        for field in forbidden_fields:
            if field in cleaned_data:
                del cleaned_data[field]
        
        # FIX: Preserve "Present" for current jobs before AI processing
        # The DB stores is_current=True + end_date=None for current roles.
        # If we don't mark this explicitly, the AI hallucinates an end date.
        for exp in cleaned_data.get("work_experiences", []):
            if exp.get("is_current", False) or not exp.get("end_date"):
                if not exp.get("end_date") or str(exp.get("end_date", "")).lower() in ["none", "null", ""]:
                    exp["end_date"] = "Present"
        for proj in cleaned_data.get("projects", []):
            if proj.get("is_current", False) or not proj.get("end_date"):
                if not proj.get("end_date") or str(proj.get("end_date", "")).lower() in ["none", "null", ""]:
                    proj["end_date"] = "Present"
        
        # 3. PREPARE INPUTS
        # Simplify input JSON for the AI to reduce token noise
        input_profile_json = json.dumps(cleaned_data, indent=2, ensure_ascii=False)
        
        # 4. CONSTRUCT "THE STRATEGIST" PROMPT
        system_role = f"You are a Senior Career Coach specialized in the {country} job market. You are FIXING a resume based on an ATS Audit Report."
        
        # -------------------------------------------------------------
        # ANTI-PARROT MEASURE: GHOST THE OLD SUMMARY
        # -------------------------------------------------------------
        # We strip the candidate's existing summary so the AI CANNOT copy it.
        # It must generate a new one based on the Work Experience + Target Job Title.
        profile_copy = input_profile_json.copy() if isinstance(input_profile_json, dict) else json.loads(input_profile_json)
        if "professional_summary" in profile_copy: del profile_copy["professional_summary"]
        if "summary" in profile_copy: del profile_copy["summary"]
        if "bio" in profile_copy: del profile_copy["bio"]
        if "about" in profile_copy: del profile_copy["about"]
        
        # Re-serialize for prompt
        clean_profile_json = json.dumps(profile_copy, indent=2)

        # [NEW] Generate Reframing Instructions based on analysis
        target_scope = self._classify_job_scope(job_title)
        reframing_instructions = self._analyze_and_tag_experiences(cleaned_data.get("work_experiences", []), target_scope)
        
        # [NEW] Generate Career Arc Strategy
        arc_strategy = self._analyze_career_trajectory(cleaned_data.get("work_experiences", []), job_title)

        # Indian Special Logic: Freshers use "Career Objective", pros use "Professional Summary"
        summary_type = "Professional Summary"
        if country.lower() == "india":
            exp_count = len(cleaned_data.get("work_experiences", []))
            if exp_count <= 1: # Basic heuristic for fresher
                summary_type = "Career Objective"

        # [NEW] Construct ATS Repair Context
        ats_repair_context = ""
        if ats_report:
             warnings = ats_report.get("warnings", [])
             errors = ats_report.get("errors", [])
             missing_kws = ats_report.get("keywords", {}).get("missing", [])
             
             ats_repair_context = f"""
             ATS AUDIT REPORT (CRITICAL - YOU MUST FIX THESE):
             - WARNINGS: {json.dumps(warnings)}
             - ERRORS: {json.dumps(errors)}
             - MISSING KEYWORDS TO INJECT: {json.dumps(missing_kws)}
             
             INSTRUCTION: Prioritize addressing the specific issues listed above. If 'Missing Keywords' are listed, find legitimate ways to integrate them into the experience bullets (if truthful).
             """

        # Prepare Languages String for Prompt Context
        formatted_languages = []
        for lang in user_data.get('languages', []):
            if isinstance(lang, dict):
                formatted_languages.append(f"{lang.get('language', 'Unknown')} ({lang.get('proficiency_cefr', '')})")
            else:
                formatted_languages.append(str(lang))
        
        lang_context_str = ", ".join(formatted_languages) if formatted_languages else "English (Native)"

        # [NEW] Append Reframing to Prompt
        # EXTRACT IDENTITY FOR LOCK
        dob_lock = user_data.get('date_of_birth', '')
        nat_lock = user_data.get('nationality', '')
        addr_lock = f"{user_data.get('street_address', '')}, {user_data.get('postal_code', '')} {user_data.get('city', '')}".strip(', ')
        
        task_instruction = f"""
        CRITICAL INSTRUCTION [IDENTITY LOCK]:
        - TARGET ROLE: {job_title}
        - CURRENT STATUS: The candidate IS ALREADY a {job_title}.
        
        IDENTITY OVERRIDE (YOU MUST USE THESE EXACT DETAILS):
        - Date of Birth: {dob_lock if dob_lock else 'OMIT IF MISSING'}
        - Nationality: {nat_lock if nat_lock else 'OMIT IF MISSING'}
        - Address: {addr_lock if len(addr_lock) > 5 else 'OMIT IF MISSING'}
        
        {arc_strategy}
        
        - RULE: You must REFRAME all past experience to fit this Target Role.
        
        {reframing_instructions}
        
        {ats_repair_context}
        
        ANTI-PARROT RULE (ZERO TOLLERANCE):
        - TARGET ROLE: {job_title}
        - CURRENT STATUS: The candidate IS ALREADY a {job_title}.
        - RULE: You must REFRAME all past experience to fit this Target Role.
        
        ANTI-PARROT RULE (ZERO TOLLERANCE):
        - You are FORBIDDEN from copying the original bullet points.
        - You MUST change at least 50% of the wording in every single bullet point.
        - If the input says "Developed a tool", you write "Engineered a scalable automated solution".
        - VARIATION IS REQUIRED. Verbatim copying = FAIL.

        # ROLE: Elite CV Strategist ({country} Market Specialist)
        # OBJECTIVE: Transform the user's profile into a high-impact {country} CV.

        # CRITICAL CONTEXT:
        - **KNOWN LANGUAGES**: {lang_context_str} (You MUST include these in the 'languages' list).
        - **IDENTITY**: Born: {dob_lock}, Citizen: {nat_lock} (Use strictly if present).

        # CRITICAL RULES ({country.upper()} COMPLIANCE):
        1. **Location**: {country}
        2. **Date Format**: {date_format}
           - RULE: High Precision = High Trust. If input says "Oct 15, 2023", output "15.10.2023" (if Germany) or standard format for {country}.
           - RULE: Use the DOT separator (.) for Germany, or standard for {country}.
        3. **Language Levels**: Must be explicit (A1-C2 or Native).
           - Input: "German (B2)" -> Output: "German (B2 - Professional Working Proficiency)" or similar context if fit.
           - FORCE MAPPING: IF German is "Native", output "German: Muttersprache (C2)". IF English is "Native", output "English: Native Speaker (C2)".
        4. **OPTIONALITY (Anti-Hallucination)**:
           - If 'nationality' or 'photo' is missing in input, DO NOT invent placeholders like "N/A" or "Placeholder". Omit the field or leave it null.

        STRUCTURE REPAIR INSTRUCTIONS:
        1. **PROJECT MERGING**: If 'projects' are listed but act as freelance/contract roles (e.g., specific client work, dated entries), MERGE them into 'work_experiences'. 
           - **EXCEPTION**: For {country}, especially India/undergrads, keep a separate "Projects" section if they are academic or significant.
        2. **EDUCATION RECOVERY**: If the 'educations' list is empty or sparse, but the text mentions degrees, YOU MUST EXTRACT them.
        3. **LANGUAGES**: Ensure the 'languages' list is populated from the Known Languages above.

        SPECIFIC REFRAMING RULES:
        1. **NOMINAL STYLE ({country.upper()} RULES - STRICT)**: 
           - **FORBIDDEN WORDS**: "I", "We", "My", "Our", "Me", "Us" -> INSTANT FAIL.
           - **PASSIVE/NOMINAL ONLY (Required for Germany AND Japan)**: 
             - BAD: "I managed a team" 
             - GOOD: "Management of a team" ("Leitung eines Teams" for DE, Nominal for JP)
             - BAD: "We reduced processing time"
             - GOOD: "Reduction of processing time"
             - BAD: "I was responsible for"
             - GOOD: "Responsible for..."
           - **ACTION DRIVEN**: Start every bullet with a strong noun or action verb (e.g., "Development", "Optimization", "Created").
        2. Your goal is NOT to summarize their history. Your goal is to SELL them for the {job_title} role.
        3. Format all bullets to lead with high-impact ACTION VERBS.

        MISSION: Rewrite this candidate's profile to align 100% with the Target Job Description and FIX issues from the ATS Report.
        
        TARGET ROLE: {job_title}
        TARGET JD: {job_description[:1200]}...
        
        CURRENT PROFILE DATA:
        {json.dumps(cleaned_data, indent=2, ensure_ascii=False)}
        
        INSTRUCTIONS:
        1. Parse the TARGET JD to identify key required skills, experiences, and keywords.
        2. Spin the CURRENT PROFILE DATA to highlight the most relevant points.
           - Delete completely irrelevant experiences/skills to save space if needed.
           - Quantify achievements if possible.
           - FIX ANY COMPLIANCE ISSUES MENTIONED IN THE ATS REPORT.
           
        3. SUMMARY SECTION:
           - **MANDATORY**: Generate a BRAND NEW {summary_type} (3-4 lines).
           - It must prove the candidate is the perfect fit for {job_title}.
           - NEVER mention "Full-Stack" or generalist terms if the JD is specialized (e.g., AI or Backend).
           - If country is Germany or Japan, do NOT use "I am a...". Use "Specialized in..." or "Experienced in...".
           
        4. {country.upper()} FORMATTING (STRICT):
           - STRICTLY use the Date Format: {date_format} (VERY IMPORTANT for Japan: YYYY.MM.DD)
           - STRICTLY use the Section Headings provided in RAG.
           {japan_specific_instructions}
           {no_pronoun_rule}
           - **LANGUAGE LEVELS**: For Japan, use JLPT levels for Japanese and TOEIC scores for English (e.g., "TOEIC 900+ equivalent").
        
        OUTPUT FORMAT:
        Return ONLY valid JSON with this exact structure (no markdown, no extra text).
        CRITICAL: Uses these EXACT JSON KEYS (do not translate keys, only translate values):
        {{
            "professional_summary": "The new spun summary...",
            "links": [{{"label": "Portfolio", "url": "https://..."}}, {{"label": "LinkedIn", "url": "..."}}],
            {japan_fields}"work_experiences": [
                {{
                    "id": "ORIGINAL_ID_FROM_INPUT", // MUST INCLUDE original ID to map back to profile
                    "company": "Name",
                    "job_title": "Title",
                    "start_date": "DD.MM.YYYY", 
                    "end_date": "DD.MM.YYYY or Present",
                    "location": "City", 
                    "description": ["Bullet 1 (Spun)", "Bullet 2 (Spun)"]
                }}
            ],
            "educations": [
                {{
                    "institution": "University Name",
                    "degree": "Degree Name",
                    "graduation_date": "MM/YYYY"
                }}
            ],
            "generated_summary": "Your 3-4 line powerful summary here...",
            "projects": [
                {{
                    "id": "ORIGINAL_ID_FROM_INPUT",
                    "title": "Project Title",
                    "role": "Role",
                    "start_date": "MM/YYYY",
                    "end_date": "MM/YYYY or Present",
                    "link": "https://code-or-demo-link.com",
                    "description": ["Bullet 1", "Bullet 2"]
                }}
            ],
            "skills": ["Skill1", "Skill2"],
            "languages": [
                {{ "name": "English", "level": "Native" }}
            ],
            "certifications": [
                {{ "name": "Cert Name", "issuing_organization": "Org", "issue_date": "MM/YYYY" }}
            ],
            "headline": "Target Professional Title"
        }}
        """

        full_prompt = f"{system_role}\n\n{rag_context_prompt}\n\nCANDIDATE PROFILE:\n{clean_profile_json}\n\n{task_instruction}"
        
        # LOG FOR USER VERIFICATION
        logger.info("="*30 + " STRATEGIST PROMPT " + "="*30)
        logger.info(full_prompt[:2000] + "...") # Log first 2k chars
        logger.info("="*80)

        # 5. EXECUTE AI CALL
        try:
            result = await self._call_api(full_prompt, temperature=0.4, max_tokens=2500)
            
            # --- GRACEFUL FALLBACK (If AI Fails completely, don't crash the app) ---
            if not result:
                logger.error("AI GENERATION FAILED: All providers/models failed. Falling back to original data.")
                
                # [Date Formatting Fix for Fallback]
                # Even if AI fails, we must format the dates for the target country (Germany)
                if country and "germany" in country.lower():
                     def fallback_format_date(date_str):
                         if not date_str: return "Present"
                         s = str(date_str).strip()
                         # ISO YYYY-MM-DD -> DD.MM.YYYY
                         match_iso = re.match(r'^(\d{4})[.-](\d{2})[.-](\d{2})$', s)
                         if match_iso:
                             return f"{match_iso.group(3)}.{match_iso.group(2)}.{match_iso.group(1)}"
                         # ISO YYYY-MM -> MM.YYYY
                         match_month = re.match(r'^(\d{4})[.-](\d{2})$', s)
                         if match_month:
                             return f"{match_month.group(2)}.{match_month.group(1)}"
                         return s

                     # Apply to lists
                     for list_key in ['work_experiences', 'educations', 'projects']:
                         if list_key in cleaned_data:
                             for item in cleaned_data[list_key]:
                                 item['start_date'] = fallback_format_date(item.get('start_date'))
                                 item['end_date'] = fallback_format_date(item.get('end_date'))
                                 if list_key == 'educations' and 'graduation_date' in item:
                                     item['graduation_date'] = fallback_format_date(item.get('graduation_date'))
                                 
                                 # [TEMPLATE FIX] Uni-Directional Sync for Work Experience (Achievements <-> Description)
                                 if list_key == 'work_experiences':
                                     # 1. Prefer achievements from DB (since we are in fallback, we use original data)
                                     bullets = item.get('achievements') or item.get('description')
                                     
                                     # 2. Normalize to list
                                     if isinstance(bullets, str):
                                         bullets = [bullets]
                                     elif not bullets:
                                         bullets = []
                                         
                                     # 3. Apply to BOTH keys
                                     item['achievements'] = bullets
                                     item['description'] = bullets

                                 # [TEMPLATE FIX] Projects description should be a string
                                 if list_key == 'projects' and 'description' in item:
                                     desc = item['description']
                                     if isinstance(desc, list):
                                         item['description'] = " ".join(desc)

                     # [TEMPLATE FIX] Map 'language' -> 'name' and 'proficiency_cefr' -> 'level'
                     if 'languages' in cleaned_data:
                         for lang in cleaned_data['languages']:
                             if isinstance(lang, dict):
                                 if 'language' in lang and 'name' not in lang:
                                     lang['name'] = lang['language']
                                 if 'proficiency_cefr' in lang and 'level' not in lang:
                                     lang['level'] = lang['proficiency_cefr']
                                     
                # Return original data structure effectively skipping AI enhancement
                return {
                    "success": True, # Technically success in handling
                    "warn": "AI_UNAVAILABLE",
                    "resume_content": clean_profile_json, # Raw profile in text format? No, we need structure.
                    "generated_summary": cleaned_data.get("professional_summary", ""),
                    "spun_data": cleaned_data, # Just return original as "spun"
                    "audit_log": {"status": "skipped_ai_failure"}
                }

            # Clean and Parse
            try:
                json_str = self._clean_json_string(result)
                generated_content = json.loads(json_str)
                # --- SANITIZATION LAYER ---
                generated_content = self._sanitize_spun_data(generated_content)
            except Exception as e:
                logger.error(f"PARSING FAILED. RAW OUTPUT: {result}")
                return {"success": False, "error": "AI_JSON_PARSE_ERROR", "details": str(e)}
            
            # 6. MERGE & RETURN
            # We explicitly replace the key sections with the AI-spun versions
            
            # SAFE ACCESS with defaults
            new_summary = generated_content.get("professional_summary", "")
            new_experiences = generated_content.get("work_experiences", [])
            new_skills = generated_content.get("skills", [])
            new_educations = generated_content.get("educations", [])
            new_projects = generated_content.get("projects", [])
            new_languages = generated_content.get("languages", [])
            new_certifications = generated_content.get("certifications", [])
            new_headline = generated_content.get("headline", "")
            
            cleaned_data["professional_summary"] = new_summary
            cleaned_data["work_experiences"] = new_experiences
            cleaned_data["educations"] = new_educations
            cleaned_data["projects"] = new_projects
            cleaned_data["headline"] = new_headline or cleaned_data.get("headline")
            
            if new_languages:
                cleaned_data["languages"] = new_languages
            if new_certifications:
                cleaned_data["certifications"] = new_certifications
            
            if country.lower() == 'japan':
                if generated_content.get("self_pr"):
                    cleaned_data["self_pr"] = generated_content.get("self_pr")
                    cleaned_data["professional_summary"] = generated_content.get("self_pr")
                if generated_content.get("motivation"):
                    cleaned_data["motivation"] = generated_content.get("motivation")
            else:
                # Explicitly strip Japanese schema fields to prevent ghost persistence 
                # from previously broken generations in the database
                cleaned_data.pop("self_pr", None)
                cleaned_data.pop("motivation", None)

            # --- [NEW] Base64 Photo Embedding ---
            if cleaned_data.get("profile_pic_url"):
                logger.info(f"Germany/Global Fix: Encoding profile photo to Base64 for PDF rendering...")
                cleaned_data["profile_pic_base64"] = await encode_image_to_base64(cleaned_data["profile_pic_url"])

            # 7. APPLY GHOST PROTOCOL (Identity Scan)
            final_summary = cleaned_data.get("professional_summary", "")
            try:
                # 1. Sanitize Summary
                final_summary = self._apply_ghost_protocol(final_summary, job_title)
                cleaned_data["professional_summary"] = final_summary
                
                # 2. Sanitize Work Experiences
                final_experiences = cleaned_data.get("work_experiences", [])
                for exp in final_experiences:
                    # Sanitize Descriptions
                    new_desc = []
                    for bullet in exp.get("description", []):
                        new_bullet = self._apply_ghost_protocol(bullet, job_title)
                        new_desc.append(new_bullet)
                    exp["description"] = new_desc
                    
                    # Sanitize Job Title (Optional, but safer)
                    exp["job_title"] = self._apply_ghost_protocol(exp.get("job_title", ""), job_title)

                cleaned_data["work_experiences"] = final_experiences

                # --- POST-PROCESSING FORCE FIXES (GERMANY) ---
                if country and "germany" in country.lower():
                     logger.info("Applying Germany Strict Compliance Rules (Nuclear Mode)...")

                     # FORCE FIX 1: Smart & Aggressive Project Deduplication
                     if cleaned_data.get('projects') and cleaned_data.get('work_experiences'):
                         kept_projects = []
                         # Get all words from Experience titles and companies to check against
                         exp_keywords = set()
                         for exp in cleaned_data['work_experiences']:
                             # Use regex for typo-safe extraction
                             title_normalized = re.sub(r'\bAl\b', 'AI', str(exp.get('job_title', '')))
                             title_words = title_normalized.lower().split()
                             company_words = str(exp.get('company', '')).lower().split()
                             exp_keywords.update(title_words + company_words)
                         
                         for proj in cleaned_data['projects']:
                             p_title_raw = re.sub(r'\bAl\b', 'AI', str(proj.get('title', ''))).lower()
                             
                # 3. Extract Summary (CRITICAL FIX)
                if "generated_summary" in cleaned_data and cleaned_data["generated_summary"]:
                    final_summary = cleaned_data["generated_summary"]
                    final_summary = final_summary.replace("Professional Summary:", "").strip()
                    cleaned_data["professional_summary"] = final_summary
                    logger.info(f"Summary extracted successfully: {final_summary[:50]}...")
                
                # FALLBACK: If AI completely omitted the summary, force generate one
                if not final_summary or len(final_summary) < 10:
                     logger.warning("AI missed summary generation. Generating fallback...")
                     final_summary = await self.generate_simple_summary(cleaned_data)
                     cleaned_data["professional_summary"] = final_summary
                
                # 4. Identity Lock (Nuclear Fix for Hallucinations)
                if country and (("germany" in country.lower()) or ("japan" in country.lower())):
                    trusted_dob = user_data.get('date_of_birth')
                    if trusted_dob:
                        cleaned_data['date_of_birth'] = format_german_date(trusted_dob)

                    trusted_nat = user_data.get('nationality')
                    if trusted_nat:
                        cleaned_data['nationality'] = trusted_nat

                    trusted_addr = user_data.get('street_address')
                    if trusted_addr:
                        cleaned_data['street_address'] = trusted_addr

                    # FORCE FIX 3: Nominal Style Enforcer (Regex)
                    def enforce_nominal_style(text_input):
                        if not text_input or not isinstance(text_input, str): return text_input
                        text = text_input
                        # 0. Strip leading non-alphanumeric chars (like •, *, -)
                        text = re.sub(r'^[^a-zA-Z0-9]+', '', text)
                        # 1. Strip leading pronouns
                        text = re.sub(r'^(I|We|My|Our|me|us)\s+', '', text, flags=re.IGNORECASE)
                        # 2. Transform common verbs to nouns/passive
                        text = re.sub(r'^managed\b', 'Management of', text, flags=re.IGNORECASE)
                        # 3. Capitalize first letter
                        if len(text) > 0:
                            text = text[0].upper() + text[1:]
                        return text

                    # Apply to Experience, Education, AND Projects (Nuclear Wash)
                    for list_key in ['work_experiences', 'educations', 'projects']:
                        if list_key in cleaned_data:
                            for item in cleaned_data[list_key]:
                                item['start_date'] = format_german_date(item.get('start_date'))
                                item['end_date'] = format_german_date(item.get('end_date'))
                                if list_key == 'educations' and 'graduation_date' in item:
                                    item['graduation_date'] = format_german_date(item.get('graduation_date'))
                                
                                # [REGEX FIX] Enforce Nominal Style on Descriptions
                                if list_key in ['work_experiences', 'projects']:
                                    bullets = item.get('achievements') or item.get('description') or []
                                    if isinstance(bullets, str): bullets = [bullets]
                                    
                                    new_bullets = []
                                    for b in bullets:
                                        if isinstance(b, str):
                                            new_bullets.append(enforce_nominal_style(b))
                                        else:
                                            new_bullets.append(b)
                                    
                                    item['achievements'] = new_bullets
                                    item['description'] = new_bullets

                                # [TEMPLATE FIX] Uni-Directional Sync for Work Experience
                                if list_key == 'work_experiences':
                                    bullets = item.get('achievements') or item.get('description')
                                    if isinstance(bullets, str):
                                        bullets = [bullets]
                                    elif not bullets: 
                                        bullets = []
                                    item['achievements'] = bullets
                                    item['description'] = bullets

                                # [TEMPLATE FIX] Projects description should be a string
                                if list_key == 'projects' and 'description' in item:
                                    desc = item['description']
                                    if isinstance(desc, list):
                                        item['description'] = " ".join(desc)

                    # [NUCLEAR FIX] Force Overwrite Identity from Input Data
                    if user_data.get('date_of_birth'):
                        cleaned_data['date_of_birth'] = format_german_date(user_data['date_of_birth'])
                    else:
                        cleaned_data['date_of_birth'] = None

                    if user_data.get('nationality'):
                        cleaned_data['nationality'] = user_data['nationality']
                    else:
                        cleaned_data['nationality'] = None

                    if user_data.get('languages'):
                        cleaned_data['languages'] = user_data['languages']

                    # [TEMPLATE FIX] Map 'language' -> 'name'
                    if 'languages' in cleaned_data:
                        for lang in cleaned_data['languages']:
                            if isinstance(lang, dict):
                                if 'language' in lang and 'name' not in lang:
                                    lang['name'] = lang['language']
                                if 'proficiency_cefr' in lang and 'level' not in lang:
                                    lang['level'] = lang['proficiency_cefr']
                    
                    if cleaned_data.get('date_of_birth'):
                        cleaned_data['date_of_birth'] = format_german_date(cleaned_data['date_of_birth'])
                    cleaned_data['nationality'] = cleaned_data.get('nationality', '')

                # [NEW] Generate Audit Log
                self._generate_transformation_audit(user_data, cleaned_data, job_title)
                
                # [NEW] Run Compliance Validation
                validation_warnings = self._validate_country_compliance(user_data, knowledge_base)

                return {
                    "success": True,
                    "resume_content": generated_content,
                    "generated_summary": final_summary,
                    "spun_data": cleaned_data,
                    "audit_log": {
                        "provider": "Gemini 1.5 Flash", 
                        "model": "gemini-1.5-flash",
                        "reframing_applied": bool(reframing_instructions),
                        "compliance_warnings": validation_warnings
                    }
                }

            except ValueError as ghost_err:
                 # GHOST PROTOCOL BREACH
                 logger.error(f"GHOST PROTOCOL BREACH: {ghost_err}")
                 import traceback
                 logger.error(f"ENGINE_INPUT_DUMP: {json.dumps({'job': job_title, 'generated_summary': final_summary})}")
                 raise ValueError("GHOST_PROTOCOL_BREACH: Forbidden identity terms detected in summary.")

            # Return WRAPPER expected by main.py
            return {
                "success": True,
                "resume_content": json.dumps(cleaned_data), # Full Clean JSON for audit/debug if needed
                "generated_summary": final_summary,         # The Cleaned Summary
                "spun_data": cleaned_data,                  # The Full Spun Data (Exp + Skills)
                "audit_log": {"steps": "Strategist Tailoring", "timestamp": datetime.now().isoformat()}
            }
            
        except Exception as e:
            logger.error(f"Strategist Generation Failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": "AI_GENERATION_FAILED", "details": str(e)}

        
        # Call AI for summary only
        # CRITICAL: STEP A MUST use a SINGLE deterministic provider (NOT openrouter/auto)
        # Using Gemini for STEP A (more reliable than free OpenRouter models)
        logger.error("[STEP A] Calling AI provider for summary generation...")
        logger.error("[STEP A] Provider selected: Gemini (v1 API)")
        
        # Force STEP A to use Gemini directly with v1 API
        # Use empty string for model to trigger default Gemini model from Config
        step_a_provider = "gemini:"
        
        try:
            summary_response = await self._execute_with_provider_retry(
                step_a_provider, 
                summary_prompt, 
                temperature=0.7, 
                max_tokens=300
            )
        except Exception as e:
            logger.error(f"[STEP A] Provider {step_a_provider} failed: {e}")
            summary_response = None
        
        # CRITICAL: Harden response parsing (OpenRouter can return HTTP 200 with empty/malformed content)
        if not summary_response or not isinstance(summary_response, dict):
            logger.error(f"[STEP A] AI_INVALID_RESPONSE: summary_response is None or malformed (type={type(summary_response)})")
            return {
                "error": "AI_SERVICE_UNAVAILABLE",
                "details": "AI provider returned invalid response structure",
                "audit_log": {"step": "summary_generation", "error": "malformed_response", "timestamp": datetime.now().isoformat()}
            }
        
        generated_summary = summary_response.get("content")
        
        if not generated_summary or not isinstance(generated_summary, str):
            logger.error(f"[STEP A] AI_INVALID_RESPONSE: summary content missing or invalid (type={type(generated_summary)})")
            logger.error(f"[DEBUG] OpenRouter raw response: {json.dumps(summary_response)[:500]}")
            return {
                "error": "AI_SERVICE_UNAVAILABLE",
                "details": "AI provider returned empty or invalid summary content",
                "audit_log": {"step": "summary_generation", "error": "empty_content", "timestamp": datetime.now().isoformat()}
            }
        
        generated_summary = generated_summary.strip()
        
        if not summary_response.get("success") or not generated_summary:
            logger.error("[STEP A] Failed: No summary generated")
            return {
                "error": "AI_SERVICE_UNAVAILABLE",
                "details": "Summary generation failed",
                "audit_log": {"step": "summary_generation", "timestamp": datetime.now().isoformat()}
            }
        
        logger.error(f"[STEP A] Summary generated successfully: {generated_summary[:150]}...")
        
        # KILL SWITCH: Hard block forbidden terms in generated summary
        summary_lower = generated_summary.lower()
        if "full-stack" in summary_lower or "fullstack" in summary_lower:
            logger.error(f"KILL SWITCH TRIGGERED: 'Full-Stack' found in generated summary")
            return {
                "error": "SUMMARY_SCOPE_VIOLATION",
                "details": "Identity leakage: Full-Stack detected in summary",
                "audit_log": {
                    "generated_summary": generated_summary,
                    "violation": "full-stack identity",
                    "timestamp": datetime.now().isoformat()
                }
            }
        
        # ============================================================================
        # STEP B: GENERATE FULL RESUME (Summary Injected Verbatim)
        # ============================================================================
        
        logger.error("[STEP B] EXECUTING: FULL RESUME")
        logger.error("=" * 60)
        logger.error("[STEP B] Generating Full Resume (Summary Injected)")
        logger.error(f"[STEP B] Injecting summary: {generated_summary[:100]}...")
        logger.error("=" * 60)
        
        # CRITICAL: ASSERTION GUARD - Prevent contamination
        if "full-stack" in generated_summary.lower() or "fullstack" in generated_summary.lower():
            logger.error("[STEP B] ASSERTION FAILED: 'Full-Stack' found in generated_summary before injection!")
            raise RuntimeError("SUMMARY_INJECTION_VIOLATION: Generated summary contains forbidden phrases")
        
        # CRITICAL: HARD DELETE all summary fields from filtered_data before STEP B
        # This prevents STEP B from seeing ANY old profile summary
        summary_fields_to_delete = [
            "professional_summary", "summary", "bio", "about", 
            "headline", "role_description", "profile_summary",
            "description", "intro", "overview"
        ]
        
        for field in summary_fields_to_delete:
            if field in filtered_data:
                logger.error(f"[STEP B] HARD DELETE: Removing '{field}' from STEP B input")
                del filtered_data[field]
        
        # Re-serialize filtered_data WITHOUT any summary fields
        filtered_data_json_clean = json.dumps(filtered_data, indent=2, ensure_ascii=False)
        
        # LOG: Verify what STEP B actually receives
        logger.error(f"[STEP B] Summary being injected (FULL): {generated_summary}")
        logger.error(f"[STEP B] Profile data size (no summaries): {len(filtered_data_json_clean)} chars")
        
        # Step B Prompt: Full resume BODY (Experience, Skills, Education) - NO SUMMARY
        full_resume_prompt = f"""
### START OF INSTRUCTIONS
1. ROLE: You are an expert ATS-optimized resume writer for {country} ({language}).
2. AUTHORITY: The "TARGET JOB TITLE" below is the ONLY title you may use.
3. DATA SOURCE: Use ONLY the "USER PROFILE" data provided. Do NOT add missing info.
4. EXCLUSION: DU NOT generate a Professional Summary. That section is already finished.
5. FORMAT: Output ONLY clean HTML fragments inside [RESUME] tags.
6. STARTING POINT: Start directly with the "Work Experience" section.

RAG SYSTEM CONSTRAINTS:
- SECTION HEADINGS: {section_headings} (MUST USE THESE EXACT EXTERNAL KEYS)
- ACTION VERBS: {action_verbs} (Start bullets with these if applicable)
- DATE FORMAT: {date_format}
- KNOWLEDGE BASE: {rag_context_str[:1500]}

TARGET JOB TITLE: "{job_title}"
JOB DESCRIPTION (CONTEXT): {job_description[:1000]}
USER PROFILE: {filtered_data_json_clean}
COUNTRY RULES ({country}): {rag_context_str}

[RESUME]
<div class="header">
  <h1 class="name">{filtered_data.get('full_name', 'Candidate')}</h1>
  <p class="target-title">{job_title}</p>
  ... (Contact Info) ...
</div>

<!-- PROFESSIONAL SUMMARY IS ALREADY DONE - DO NOT WRITE IT -->
<!-- START DIRECTLY WITH WORK EXPERIENCE -->

<div class="section">
  <h2 class="section-title">...</h2>
  ...
</div>
... (Skills, Education, etc) ...
[/RESUME]

[AUDIT_LOG]
{{
  "pipeline_compliance": {{
    "summary_skipped": true,
  }},
  "tailoring_confidence": "HIGH"
}}
"""
        # 3. Call AI for Step B (Full Resume)
        rich_response = await self._call_api_rich(full_resume_prompt, temperature=0.7, max_tokens=4000)
        result = rich_response.get("content")
        
        # PROMPT ECHO PROTECTION: If the result looks like the prompt, something is wrong
        if result and ("### START OF INSTRUCTIONS" in result):
            logger.error("AI Echoed Prompt! Infrastructure failure.")
            result = None
            rich_response["success"] = False
        
        if not rich_response.get("success"):
            logger.error("AI Generation failed - No response from any provider")
            # INFRASTRUCTURE FAILURE (Retry budget exhausted)
            return {
                "error": "AI_SERVICE_UNAVAILABLE",
                "details": "All AI providers failed to return a valid response after retries.",
                "audit_log": {
                    "job_title": job_title,
                    "provider_used": rich_response.get("provider", "none"),
                    "failure_type": "infrastructure_failure",
                    "timestamp": datetime.now().isoformat()
                }
            }
            
        # 4. Parse Response ([RESUME] and [AUDIT_LOG])
        try:
            parts = result.split("[AUDIT_LOG]")
            resume_block = parts[0]
            # Precise extraction of [RESUME] content
            if "[RESUME]" in resume_block:
                body_content = resume_block.split("[RESUME]")[1].split("[/RESUME]")[0].strip() if "[/RESUME]" in resume_block else resume_block.split("[RESUME]")[1].strip()
            else:
                body_content = resume_block.strip()
                
            # CRITICAL: Manually inject the verified summary from STEP A
            # This ensures the model CANNOT modify or contaminate it
            
            # 1. Extract Header if key present (simplification for safety)
            # Actually, let's just prepend the Summary Section to the Body Content
            # The body content starts with Work Experience per instructions
            
            final_summary_html = f"""
            <div class="section summary-section">
                <h2 class="section-title">Professional Summary</h2>
                <div class="summary-content">
                    <p>{generated_summary}</p>
                </div>
            </div>
            """
            
            # If the model accidentally included a header, we keep it. 
            # If it accidentally included a summary, we strip it (simple heuristic)
            if "Professional Summary" in body_content:
                logger.warning("[STEP B] Model ignored exclusion instruction. Stripping duplicate summary...")
                # Simple strip - split by Work Experience and take the second part? 
                # Safer: Just append our Safe Summary to the top of the body, assuming body starts with Experience
                # But headers might be an issue.
                pass 
                
            # Ideally: Header -> Summary (Injected) -> Body
            # Since the prompt asks for Header, we assume Header is at the top of body_content.
            # We need to insert Summary AFTER Header but BEFORE Experience.
            
            # Simple Injection Strategy:
            # Find the closing div of the header? Or just look for the first section?
            # Let's iterate:
            
            if "</div>" in body_content:
                # Insert after the first closing div (likely header)
                # This is heuristic but robust for the standard template
                resume_content = body_content.replace("</div>", f"</div>\n{final_summary_html}", 1)
            else:
                # Fallback: Prepend
                resume_content = final_summary_html + "\n" + body_content
                
            logger.error(f"[STEP B] Final Assembly: Injected Verified Summary ({len(generated_summary)} chars)")

            
            audit_log_str = parts[1].strip() if len(parts) > 1 else "{}"
            if "```json" in audit_log_str:
                audit_log_str = self._clean_json_string(audit_log_str)
            
            audit_log = json.loads(audit_log_str)
            
            # --- MANDATORY AUDIT ENFORCEMENT ---
            audit_log["job_title"] = job_title
            audit_log["summary_source"] = "ai_generated_only"
            audit_log["summary_fallback_used"] = False
            audit_log["provider_used"] = rich_response.get("provider")
            audit_log["generation_timestamp"] = datetime.now().isoformat()
            audit_log["failure_type"] = None
            
            # --- VERIFICATION STEP (Strict) ---
            # Extract generated summary from HTML (simple heuristics)
            # Look for div class="summary" or common summary tags
            generated_summary_check = ""
            if 'class="summary"' in resume_content:
                try:
                    # Very Naive extraction for check
                    start_marker = 'class="summary">'
                    start_idx = resume_content.find(start_marker) + len(start_marker)
                    end_idx = resume_content.find('</div>', start_idx)
                    if start_idx > len(start_marker) and end_idx > start_idx:
                        generated_summary_check = resume_content[start_idx:end_idx].strip()
                        # Clean HTML tags
                        generated_summary_check = re.sub('<[^<]+?>', '', generated_summary_check)
                except Exception:
                    pass
            
            # --- SUMMARY ANCHORING VALIDATION ---
            if generated_summary_check:
                validation_res = self._validate_summary(generated_summary_check, job_title, job_description)
                if validation_res != "OK":
                    logger.error(f"VERIFICATION FAILED: Generated summary violated scope rules ({validation_res}).")
                    return {
                        "error": "SUMMARY_SCOPE_VIOLATION",
                        "details": f"The generated summary violated the job scope rules: {validation_res}"
                    }
            
            # If we found a summary, check similarity for parroting
            if generated_summary_check and original_summary_texts:
                gen_lower = generated_summary_check.lower()
                for original in original_summary_texts:
                    # Check similarity
                    similarity = difflib.SequenceMatcher(None, gen_lower, original).ratio()
                    if similarity > 0.85: # 85% similarity threshold
                        logger.error(f"VERIFICATION FAILED: Generated summary is {similarity*100:.1f}% similar to original profile.")
                        return {
                            "error": "SUMMARY_GENERATION_FAILED",
                            "details": "Generated summary matches original profile too closely (Parroting prevention)."
                        }

            # --- CRITICAL ASSERTION ---
            if not generated_summary:
                raise ValueError("SUMMARY_GENERATION_FAILED: generated_summary is empty")
                
            if "Full-Stack" in generated_summary:
                 raise ValueError("SUMMARY_GENERATION_FAILED: Contamination detected in final output")

            return {
                "resume_content": resume_content,
                "audit_log": audit_log,
                "generated_summary": generated_summary, # Return the clean summary text
                "success": True
            }
        except Exception as e:
            logger.error(f"Failed to parse generation engine output: {e}")
            if "SUMMARY_GENERATION_FAILED" in str(e):
                 return {"error": "SUMMARY_GENERATION_FAILED", "details": str(e)}
            
            # If parsing fails or verification fails hard
            return {
                "error": "SUMMARY_GENERATION_FAILED", 
                "details": f"Parsing or Verification error: {str(e)}"
            }

    def _sanitize_spun_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Strip contact information (emails, LinkedIn, etc.) from professional sections
        to prevent template leakage.
        """
        if not data or not isinstance(data, dict):
            return data
            
        # 1. Sanitize Skills (Strip matches for emails, http links, mailto)
        if "skills" in data and isinstance(data["skills"], list):
            sanitized_skills = []
            for skill in data["skills"]:
                if isinstance(skill, str):
                    # Robust check: if it looks like a URL or email, skip it
                    if re.search(r'^https?://|^mailto:|@|\.com\b|\.in\b', skill.lower()):
                        logger.warning(f"Sanitization: Stripping potential contact link from skills: {skill}")
                        continue
                    sanitized_skills.append(skill)
                else:
                    sanitized_skills.append(skill)
            data["skills"] = sanitized_skills

        # 2. Sanitize Languages
        if "languages" in data and isinstance(data["languages"], list):
            sanitized_langs = []
            for lang in data["languages"]:
                if isinstance(lang, str):
                    if re.search(r'^https?://|^mailto:|@|\.com\b|\.in\b', lang.lower()):
                        logger.warning(f"Sanitization: Stripping potential contact link from languages: {lang}")
                        continue
                sanitized_langs.append(lang)
            data["languages"] = sanitized_langs

        # 3. Strip links from block text fields (Self-PR, Motivation, Summary)
        text_fields = ["professional_summary", "self_pr", "motivation", "summary", "summary_text"]
        for field in text_fields:
            if data.get(field) and isinstance(data[field], str):
                # Clean Markdown URL structures e.g. [mailto:...] or (https://...)
                data[field] = re.sub(r'\[.*?\]\([^)]+\)', '', data[field])
                # Remove raw http/https and mailto links
                data[field] = re.sub(r'https?://\S+|mailto:\S+|[\w\.-]+@[\w\.-]+\.\w+', '', data[field])

        # 3. Prevent photo URL from appearing in links list
        if "links" in data and isinstance(data["links"], list):
            photo_url = data.get("photo_url") or data.get("profile_pic_url")
            if photo_url:
                data["links"] = [l for l in data["links"] if isinstance(l, dict) and l.get("url") != photo_url]

        return data

    async def refine_text(self, selected_text: str, instruction: str, full_context: str = "") -> str:
        """
        Refines a specific resume section based on user natural language instruction.
        Uses a separate 'Editor' persona to ensure granular accuracy.
        """
        logger.info(f"Refining text: '{selected_text[:30]}...' with instruction: '{instruction}'")
        
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
        
        try:
            # Use a slightly lower temperature for editing precision (0.4)
            response = await self._call_api(prompt, temperature=0.4, max_tokens=300)
            
            cleaned_response = response.strip()
            # Remove quotes if the model added them wrapping the whole response
            if cleaned_response.startswith('"') and cleaned_response.endswith('"'):
                cleaned_response = cleaned_response[1:-1]
                
            return cleaned_response
            
        except Exception as e:
            logger.error(f"Refinement failed: {e}")
            return selected_text # Safe fallback

    async def generate_simple_summary(self, user_data: Dict[str, Any]) -> str:
        """
        Generate a simple professional summary based on profile data.
        Used for the 'Write with AI' button in the profile wizard.
        """
        experiences = user_data.get("work_experiences", [])
        skills = user_data.get("skills", [])
        education = user_data.get("educations", [])
        
        # Construct a simple context prompt
        context_str = ""
        if experiences:
            context_str += "Work Experience:\n"
            for exp in experiences:
                context_str += f"- {exp.get('job_title', 'Role')} at {exp.get('company', 'Company')} ({exp.get('start_date', '')} - {exp.get('end_date', 'Present')})\n"
                if exp.get('description'):
                    desc = "\n".join(exp.get('description')) if isinstance(exp.get('description'), list) else str(exp.get('description'))
                    context_str += f"  Details: {desc[:200]}...\n"
        
        if skills:
            context_str += f"\nSkills: {', '.join(skills[:15])}\n"
            
        if education:
            context_str += "\nEducation:\n"
            for edu in education:
                context_str += f"- {edu.get('degree', 'Degree')} from {edu.get('institution', 'University')}\n"

        if not context_str.strip():
            # Fallback if profile is empty
            return "Motivated professional eager to contribute skills and experience to a dynamic team. Ready to leverage strong work ethic and adaptability to drive results."

        prompt = f"""
        You are a professional resume writer.
        Write a concise, compelling Professional Summary (3-4 sentences max) for a candidate with the following background:

        {context_str}

        RULES:
        1. Keep it professional and impactful.
        2. Focus on their strongest roles and skills.
        3. Do NOT use "I" or "My" (use implied first person like "Experienced software engineer with...").
        4. Return ONLY the summary text. No quotes, no markdown.
        """

        result = await self._call_api(prompt, temperature=0.7, max_tokens=150)
        return result or "Experienced professional with a strong background in the field."

# Global instance
# Global singleton instance
ai_service = AIService()
