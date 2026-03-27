import json
import os
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

RAG_SCHEMAS_DIR = os.getenv("RAG_SCHEMAS_DIR", "rag_schemas")

class RAGRuleLoader:
    """
    Service to load country-specific resume rules from the RAG knowledge base.
    This provides the 'Source of Truth' for the Compliance Validator and Auto-Correction layers.
    """
    
    @staticmethod
    def load_country_rules(country: str, language_code: str = "en_US") -> Dict[str, Any]:
        """
        Load rules for a specific country and language.
        """
        country_lower = country.lower()
        kb_path = os.path.join(RAG_SCHEMAS_DIR, country_lower, "knowledge_base.json")
        lang_path = os.path.join(RAG_SCHEMAS_DIR, country_lower, f"{language_code}.json")
        
        rules = {
            "section_order": [],
            "date_format": "YYYY.MM.DD",
            "required_fields": [],
            "language_levels": {},
            "formatting_rules": {},
            "mandatory_sections": []
        }
        
        # 1. Load Knowledge Base (Structural Rules)
        if os.path.exists(kb_path):
            try:
                with open(kb_path, "r", encoding="utf-8") as f:
                    kb = json.load(f)
                    rules["section_order"] = kb.get("cv_structure", {}).get("order", [])
                    rules["date_format"] = kb.get("cultural_rules", {}).get("date_format", "YYYY.MM.DD")
                    rules["required_fields"] = kb.get("cv_structure", {}).get("mandatory_sections", {}).get("personal_info", {}).get("required", [])
                    rules["mandatory_sections"] = list(kb.get("cv_structure", {}).get("mandatory_sections", {}).keys())
            except Exception as e:
                logger.error(f"Error loading KB rules from {kb_path}: {e}")
        
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

# Singleton instance
rag_rule_loader = RAGRuleLoader()
