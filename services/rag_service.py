import os
import json
import logging
from typing import Dict, Any, Optional
from cachetools import TTLCache, cached

# Configure logging
logger = logging.getLogger(__name__)

# Standardize RAG pathing for production (Render/Docker)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAG_SCHEMAS_DIR = os.getenv("RAG_SCHEMAS_DIR", os.path.join(BASE_DIR, "rag_schemas"))

# TTL Cache for RAG data (1 hour = 3600 seconds)
# Allows updating schemas in production without restarting the service
knowledge_base_cache = TTLCache(maxsize=10, ttl=3600)
language_template_cache = TTLCache(maxsize=20, ttl=3600)

class RAGService:
    @staticmethod
    def map_language_code(language_input: str, country: str) -> str:
        language_map = {
            "Germany": {"german": "de_DE", "deutsch": "de_DE", "english": "en_DE"},
            "India": {"english": "en_IN", "hindi": "hi_IN"},
            "Japan": {"english": "en_US", "japanese": "ja_JP", "日本語": "ja_JP"}
        }
        country_map = language_map.get(country, {})
        lang_lower = language_input.lower()
        return country_map.get(lang_lower, "en_US")

    @staticmethod
    @cached(cache=knowledge_base_cache)
    def load_knowledge_base(country: str) -> dict:
        country_lower = country.lower()
        kb_file = os.path.join(RAG_SCHEMAS_DIR, country_lower, "knowledge_base.json")
        
        if os.path.exists(kb_file):
            try:
                with open(kb_file, 'r', encoding='utf-8') as f:
                    kb = json.load(f)
                    logger.info(f"RAG: Loaded knowledge base: {kb_file} (TTL Cache)")
                    return kb
            except Exception as e:
                logger.error(f"RAG: Failed to load knowledge base {kb_file}: {e}")
        
        logger.warning(f"RAG: Knowledge base not found: {kb_file}, using defaults")
        return {
            "country": country,
            "ats_optimization": {"fonts": ["Arial", "Calibri", "Times New Roman"], "file_format": "PDF"},
            "cv_structure": {"max_pages": 2}
        }

    @staticmethod
    @cached(cache=language_template_cache)
    def load_language_template(country: str, language_code: str) -> dict:
        country_lower = country.lower()
        lang_file = os.path.join(RAG_SCHEMAS_DIR, country_lower, f"{language_code}.json")
        
        if os.path.exists(lang_file):
            try:
                with open(lang_file, 'r', encoding='utf-8') as f:
                    template = json.load(f)
                    logger.info(f"RAG: Loaded language template: {lang_file} (TTL Cache)")
                    return template
            except Exception as e:
                logger.error(f"RAG: Failed to load language template {lang_file}: {e}")
                
        logger.warning(f"RAG: Language template not found: {lang_file}, using defaults")
        is_german = language_code.startswith("de")
        return {
            "language": language_code,
            "section_headings": {"experience": "Berufserfahrung" if is_german else "Professional Experience",
                                 "education": "Ausbildung" if is_german else "Education",
                                 "skills": "Kenntnisse" if is_german else "Skills"},
            "action_verbs": ["Entwickelte", "Koordinierte"] if is_german else ["Developed", "Coordinated"],
            "date_format": "DD.MM.YYYY" if is_german else "MM/YYYY",
            "example_bullets": []
        }

    @staticmethod
    def get_complete_rag(country: str, language: str) -> dict:
        lang_code = RAGService.map_language_code(language, country)
        knowledge_base = RAGService.load_knowledge_base(country)
        language_template = RAGService.load_language_template(country, lang_code)
        
        return {
            "knowledge_base": knowledge_base, 
            "language_template": language_template,
            "labels": language_template.get("labels", {}),
            "section_headings": language_template.get("section_headings", {}),
            "country": country, 
            "language": language, 
            "language_code": lang_code
        }


