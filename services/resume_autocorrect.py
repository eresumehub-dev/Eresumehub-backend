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
        Specific fixes for the German market (Nominal style, no pronouns, DD.MM.YYYY).
        """
        # 1. Strip Pronouns from Summary
        if data.get("professional_summary"):
             data["professional_summary"] = PRONOUNS_REGEX.sub("", data["professional_summary"]).strip()
        
        # 2. Date Formatting Enforcement (YYYY-MM-DD -> DD.MM.YYYY)
        for job in data.get("work_experiences", []):
            for d_key in ["start_date", "end_date"]:
                if job.get(d_key):
                    job[d_key] = ResumeAutocorrect._format_date_german(job[d_key])
        
        return data

    @staticmethod
    def _format_date_german(d: str) -> str:
        if not d: return d
        s = str(d).strip()
        if s.lower() in ['present', 'current', 'now', 'today']: return "Present"
        
        # Normalize slashes/dashes
        s = s.replace('/', '.').replace('-', '.')
        
        # 2020.01.01 -> 01.01.2020
        m = re.match(r'^(\d{4})\.(\d{2})\.(\d{2})$', s)
        if m: return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
        
        # 2020.01 -> 01.2020
        m = re.match(r'^(\d{4})\.(\d{2})$', s)
        if m: return f"{m.group(2)}.{m.group(1)}"
        
        # Already DD.MM.YYYY or MM.YYYY? Just normalize dots
        s = re.sub(r'\.+', '.', s)
        return s

# Singleton instance
resume_autocorrect = ResumeAutocorrect()
