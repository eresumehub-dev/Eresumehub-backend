import json
import os
import re
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# CEFR Level Regex (A1-C2) - looks for A1, A2, B1, B2, C1, C2 bound by word boundaries or parentheses
CEFR_REGEX = re.compile(r'\b(A1|A2|B1|B2|C1|C2)\b', re.IGNORECASE)

class ResumeComplianceValidator:
    """
    Validates user data against country-specific rules before resume generation.
    Driven by knowledge_base.json rules.
    """
    
    @staticmethod
    def _log_debug(message: str):
        try:
            with open("validation_trace.log", "a", encoding="utf-8") as f:
                f.write(f"{message}\n")
        except:
            pass

    @staticmethod
    def _load_rules(country: str) -> Dict[str, Any]:
        """
        Load knowledge base for the specified country.
        Now supports standard mapping and graceful fallback.
        """
        # 1. Normalize and Map Country (v16.4.19)
        c_norm = country.strip().lower()
        if c_norm == 'dach':
            c_norm = 'germany'
        
        # 2. Case-Insensitive Directory Match (Essential for Render/Linux)
        # We look for a directory that matches the normalized name
        actual_dir = None
        if os.path.exists(base_dir):
            for d in os.listdir(base_dir):
                if d.lower() == c_norm and os.path.isdir(os.path.join(base_dir, d)):
                    actual_dir = d
                    break

        if actual_dir:
            country_dir = os.path.join(base_dir, actual_dir)
            ResumeComplianceValidator._log_debug(f"[Validator] Loading rules for country: {country} (Mapped: {actual_dir}) from {country_dir}")
            kb_path = os.path.join(country_dir, "knowledge_base.json")
            if os.path.exists(kb_path):
                try:
                    with open(kb_path, 'r', encoding='utf-8') as f:
                        rules = json.load(f)
                        ResumeComplianceValidator._log_debug(f"[Validator] Rules loaded successfully. Keys: {list(rules.keys())}")
                        return rules
                except Exception as e:
                    ResumeComplianceValidator._log_debug(f"Error loading rules for {country}: {e}")
                    return {}
            else:
                 ResumeComplianceValidator._log_debug(f"[Validator] knowledge_base.json not found in {country_dir}")
        else:
             ResumeComplianceValidator._log_debug(f"[Validator] Directory not found: {country_dir}")
        
        # 2. Add mappings if needed (e.g. "United States" -> "USA") - For now, fallback to empty
        return {}

    @staticmethod
    def validate(user_data: Dict[str, Any], country: str = "Germany") -> Dict[str, Any]:
        """
        Validate user_data against dynamic RAG rules for the country.
        Returns: { "valid": bool, "errors": [ { "field": str, "message": str, "code": str } ] }
        """
        ResumeComplianceValidator._log_debug(f"[Validator] Validating for {country}. User Data Keys: {list(user_data.keys())}")
        rules = ResumeComplianceValidator._load_rules(country)
        
        # If no rules found (e.g. USA), we assume valid (allow generation)
        if not rules:
            ResumeComplianceValidator._log_debug(f"[Validator] No rules found for {country}. Passing.")
            return {"valid": True, "errors": []}

        errors = []
        required_langs = rules.get("required_languages", [])
        ResumeComplianceValidator._log_debug(f"[Validator] Required Languages: {required_langs}")
        
        # Navigate to mandatory sections: cv_structure -> mandatory_sections
        mandatory_structure = rules.get("cv_structure", {}).get("mandatory_sections", {})
        
        # --- 1. Generic Section Validation ---
        
        # Education
        if "education" in mandatory_structure:
            education = user_data.get("educations") or user_data.get("education", [])
            if not education or len(education) == 0:
                 errors.append({
                    "field": "education",
                    "message": "Education history is a mandatory section.",
                    "code": "MISSING_EDUCATION"
                })
        
        # Experience
        if "experience" in mandatory_structure:
            # We don't strictly enforce experience existence for freshers in some countries, 
            # but if it's in "mandatory_sections", we check. 
            # For now, let's just check non-empty list if user has > 0 experience years in profile? 
            # Simpler: If strict, we enforce. But RAG says "Work Experience" is mandatory order.
            # Let's check keys.
            pass # Skipping strict experience check for now as it prevents freshers
            
        # Location (Flexible lookup for header/personal_info)
        personal_reqs = mandatory_structure.get("personal_info", {}).get("required", [])
        header_reqs = mandatory_structure.get("header", {}).get("required", [])
        all_contact_reqs = personal_reqs + header_reqs
        
        location_required = any(k for k in ["City", "Location", "Current City and State"] if k in all_contact_reqs)
        
        if location_required:
             # Check multiple possible data paths for location
             city = user_data.get("city") or user_data.get("contact", {}).get("city") or user_data.get("contact", {}).get("location")
             if not city or len(str(city).strip()) < 2:
                errors.append({
                    "field": "location",
                    "message": f"City/Location is required for {country} resumes.",
                    "code": "MISSING_LOCATION"
                })

        # Date of Birth (Germany and others)
        dob_required = any(k for k in ["Date of Birth", "Birthday"] if k in all_contact_reqs)
        if dob_required:
            dob = user_data.get("date_of_birth") or user_data.get("contact", {}).get("date_of_birth")
            if not dob:
                errors.append({
                    "field": "date_of_birth",
                    "message": f"Date of Birth is mandatory for {country} resumes (Anti-discrimination laws do not apply to the same extent as US/UK).",
                    "code": "MISSING_DOB"
                })

        # Nationality (Germany and others)
        nationality_required = any(k for k in ["Nationality", "Citizenship"] if k in all_contact_reqs)
        if nationality_required:
            nat = user_data.get("nationality") or user_data.get("contact", {}).get("nationality")
            if not nat:
                errors.append({
                    "field": "nationality",
                    "message": f"Nationality is mandatory for {country} resumes.",
                    "code": "MISSING_NATIONALITY"
                })

        # Projects
        if "projects" in mandatory_structure:
            projects = user_data.get("projects", [])
            if not projects or len(projects) == 0:
                 errors.append({
                    "field": "projects",
                    "message": f"Projects section is mandatory for {country} (especially for freshers/students).",
                    "code": "MISSING_PROJECTS"
                })

        # --- 2. Language Validation (Dynamic) ---
        if required_langs:
            languages = user_data.get("languages", [])
            ResumeComplianceValidator._log_debug(f"[Validator] User Languages: {languages}")
            present_languages = set()
            
            for lang_item in languages:
                name = ""
                if isinstance(lang_item, str):
                    name = lang_item
                elif isinstance(lang_item, dict):
                    name = lang_item.get("name") or lang_item.get("language", "")
                present_languages.add(name.lower())
            
            ResumeComplianceValidator._log_debug(f"[Validator] Normalized Present Languages: {present_languages}")
            
            # Pull contextual info from RAG for richer error messages
            job_market = rules.get("job_market_info", {})
            lang_requirement_context = job_market.get("language_requirement", "")
            
            for required in required_langs:
                # Check fuzzy match
                found = False
                req_lower = required.lower()
                for present in present_languages:
                    if req_lower in present or present in req_lower:
                        found = True
                        break
                
                if not found:
                    ResumeComplianceValidator._log_debug(f"[Validator] Missing required language: {required}")
                    
                    # Build a rich, contextual error message
                    msg = f"{required} language proficiency is required for {country} resumes."
                    if lang_requirement_context:
                        msg += f" Employers expect: {lang_requirement_context}."
                    msg += f" Add {required} to your profile languages before generating."
                    
                    errors.append({
                        "field": "languages",
                        "message": msg,
                        "code": f"MISSING_{required.upper()}_LANGUAGE"
                    })

        # --- 3. Proficiency Level Check (Existing Logic) ---
        language_format = mandatory_structure.get("skills", {}).get("language_format")
        if language_format and "A1-C2" in language_format:
             pass 
             
        # --- 4. EXPLICIT JAPANESE COMPLIANCE ---
        if country.lower() == "japan":
            # 2. Check for Mandatory Sections (Self-PR, Motivation, Certifications)
            # Use rules from RAGRuleLoader via its instance or direct check if needed
            # For now, we enforce these directly as per the User's Upgrade Plan
            if not user_data.get("self_pr") and not user_data.get("professional_summary"):
                 errors.append({"field": "self_pr", "message": "Missing 'Self-PR (自己PR)' section. This is required for Japan resumes.", "code": "MISSING_SELF_PR"})
            
            if not user_data.get("motivation"):
                 errors.append({"field": "motivation", "message": "Missing 'Motivation (志望動機)' section. Explaining why you want the role is critical in Japan.", "code": "MISSING_MOTIVATION"})

            if not user_data.get("certifications") and not user_data.get("qualifications"):
                 errors.append({"field": "certifications", "message": "Qualifications & Licenses section is missing. Add certifications/licenses.", "code": "MISSING_QUALIFICATIONS"})
            
            # 3. Check Language level format (Reject CEFR A1-C2 for Japanese specifically)
            langs = user_data.get("languages", [])
            has_japanese_cefr = False
            
            for lang in langs:
                l_name = ""
                l_level = ""
                if isinstance(lang, str):
                    l_name = lang
                    l_level = lang
                elif isinstance(lang, dict):
                    l_name = str(lang.get("name") or lang.get("language", "")).lower()
                    l_level = str(lang.get("level") or lang.get("proficiency_cefr") or "")
                
                # If it's Japanese, we ARE strict
                if "japanese" in l_name:
                    if CEFR_REGEX.search(l_level):
                         has_japanese_cefr = True
                         break
                
            if has_japanese_cefr:
                 errors.append({"field": "languages", "message": "For Japanese language, use JLPT (e.g., N1, N2) levels rather than CEFR (A1-C2) for Japan resumes.", "code": "NON_STANDARD_JAPANESE_LEVEL"})

            # 4. Check for First-Person Pronouns in Summary/Self-PR/Motivation
            # (Self-PR/Professional Summary usually share the same content in our pipeline)
            for text_field in ["professional_summary", "self_pr", "motivation"]:
                val = user_data.get(text_field, "")
                if val and isinstance(val, str):
                    # Simple check for "I ", " me ", " my "
                    if re.search(r"\b(I|me|my)\b", val, re.IGNORECASE):
                        errors.append({
                            "field": text_field, 
                            "message": f"First-person pronouns detected in {text_field}. Japanese resumes must be written in a formal, third-person/nominal style.",
                            "code": "PRONOUNS_DETECTED"
                        })

        ResumeComplianceValidator._log_debug(f"[Validator] Validation Result: {len(errors) == 0}, Errors: {errors}")
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
