import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# CEFR Regex for detection
CEFR_REGEX = re.compile(r'\b([A-C][1-2])\b', re.IGNORECASE)
PRONOUNS_REGEX = re.compile(r'\b(I|me|my|mine|we|us|our|ours)\b', re.IGNORECASE)

class ResumeAutocorrect:
    """
    Service to fix minor compliance issues automatically instead of failing generation.
    Focuses on tone, language levels, and date formatting.
    """
    
    @staticmethod
    def autocorrect_for_country(resume_data: Dict[str, Any], country: str) -> Dict[str, Any]:
        """
        Apply country-specific auto-corrections.
        """
        country_lower = country.lower()
        
        if country_lower == "japan":
            return ResumeAutocorrect._autocorrect_japan(resume_data)
        elif country_lower == "germany":
            return ResumeAutocorrect._autocorrect_germany(resume_data)
            
        return resume_data
    
    @staticmethod
    def _autocorrect_japan(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Specific fixes for the Japan market.
        """
        # 1. Fix Language Levels (English C2 -> TOEIC 900+ equivalent)
        langs = data.get("languages", [])
        for lang in langs:
            if isinstance(lang, dict):
                l_name = str(lang.get("name") or lang.get("language", "")).lower()
                l_level = str(lang.get("level") or lang.get("proficiency_cefr") or "")
                
                if "english" in l_name and CEFR_REGEX.search(l_level):
                    # Replace with TOEIC equivalent as recommended by RAG
                    lang["level"] = "TOEIC 900+ equivalent proficiency"
                    lang["proficiency_cefr"] = "TOEIC 900+"
        
        # 2. Strip Pronouns from Summary/Self-PR
        for key in ["professional_summary", "self_pr", "motivation"]:
            if data.get(key):
                original = data[key]
                # Replace "I am a..." or "I led..." with nominal phrases or just remove "I "
                # Simple replacement for start of sentences
                fixed = original.replace("I am ", "Experienced ").replace("I have ", "Extensive experience in ")
                fixed = fixed.replace("I ", "").replace("My ", "").replace("my ", "")
                # More robust regex replacement
                fixed = PRONOUNS_REGEX.sub("", fixed)
                data[key] = fixed.strip()

        # 3. Ensure Date Formatting (YYYY.MM.DD)
        # This is mostly handled by the AI, but we can enforce it here if needed.
        # For now, we trust the AI more than a regex for complex date strings.
        
        return data

    @staticmethod
    def _autocorrect_germany(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Specific fixes for the German market (Nominal style, no pronouns).
        """
        # Strip Pronouns from Summary
        if data.get("professional_summary"):
             data["professional_summary"] = PRONOUNS_REGEX.sub("", data["professional_summary"]).strip()
        
        return data

# Singleton instance
resume_autocorrect = ResumeAutocorrect()
