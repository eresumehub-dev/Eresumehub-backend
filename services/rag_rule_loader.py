import json
import os
import logging
import functools
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class ConfigurationError(Exception):
    pass

from app_settings import Config

RAG_SCHEMAS_DIR = Config.RAG_SCHEMAS_DIR

@functools.lru_cache(maxsize=32)
def _load_country_rules_cached(country: str, language_code: str = "en_US") -> Dict[str, Any]:
    """
    Internal cached helper for loading country rules.
    """
    country_lower = country.lower()
    rag_schemas_dir = RAG_SCHEMAS_DIR
    
    kb_path = os.path.join(rag_schemas_dir, country_lower, "knowledge_base.json")
    lang_path = os.path.join(rag_schemas_dir, country_lower, f"{language_code}.json")
    
    rules = {
        "section_order": [],
        "date_format": "YYYY.MM.DD",
        "required_fields": [],
        "language_levels": {},
        "formatting_rules": {},
        "mandatory_sections": []
    }
    
    # 1. Load Knowledge Base (Structural Rules)
    if not os.path.exists(kb_path):
        raise ConfigurationError(f"RulesNotFound: Knowledge base for {country} missing at {kb_path}")
        
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            kb = json.load(f)
            rules["section_order"] = kb.get("cv_structure", {}).get("order", [])
            rules["date_format"] = kb.get("cultural_rules", {}).get("date_format", "YYYY.MM.DD")
            rules["required_fields"] = kb.get("cv_structure", {}).get("mandatory_sections", {}).get("personal_info", {}).get("required", [])
            rules["mandatory_sections"] = list(kb.get("cv_structure", {}).get("mandatory_sections", {}).keys())
    except Exception as e:
        logger.error(f"Error loading KB rules from {kb_path}: {e}")
        raise ConfigurationError(f"Failed to load knowledge base rules for {country}") from e
    
    # 2. Load Language Template (Localized Rules)
    if os.path.exists(lang_path):
        try:
            with open(lang_path, "r", encoding="utf-8") as f:
                lang = json.load(f)
                rules["language_levels"] = lang.get("language_levels", {})
                rules["formatting_rules"] = lang.get("formatting_rules", {})
                rules["section_headings"] = lang.get("section_headings", {})
        except Exception as e:
            logger.error(f"Error loading Language rules from {lang_path}: {e}")
            
    return rules

class RAGRuleLoader:
    """
    Service to load country-specific resume rules from the RAG knowledge base.
    This provides the 'Source of Truth' for the Compliance Validator and Auto-Correction layers.
    """
    
    @staticmethod
    def load_country_rules(country: str, language_code: str = "en_US") -> Dict[str, Any]:
        """
        Load rules for a specific country and language.
        Uses a module-level LRU cache for Python descriptor compatibility.
        """
        return _load_country_rules_cached(country, language_code)

# Singleton instance
rag_rule_loader = RAGRuleLoader()
