"""
Country-Specific Resume Rules & Configurations.
This file acts as the single source of truth for country logic,
used by both the AI prompt injection and the frontend display (via API).
"""

COUNTRY_RULES = {
    "Germany": {
        "culture_context": "Germany values precision, facts, and formal qualifications. Resumes (Lebenslauf) should be tabular, factual, and chronological.",
        "formatting": {
            "max_pages": 2,
            "date_format": "DD.MM.YYYY",
            "photo_required": True,
            "layout": "Tabular, clean, reverse chronological"
        },
        "sections": {
            "personal_info": ["dob", "nationality", "marital_status"],
            "education": "High importance, include grades if good",
            "experience": "Focus on concrete responsibilities"
        },
        "strengths": [
            "Clear, tabular structure (Lückenlos)",
            "Professional photo included",
            "Discrete, factual tone"
        ],
        "warnings": [
            "Missing date of birth (common in DE)",
            "Structure is too creative/chaotic",
            "Missing photo (highly recommended)"
        ],
        "keywords": ["Zuverlässigkeit", "Teamfähigkeit", "Strukturiert", "Verantwortung"]
    },
    "India": {
        "culture_context": "India's market is competitive. Resumes should highlight technical skills, education prestige, and quantifiable achievements. Format varies but 2-page resumes are standard for seniors.",
        "formatting": {
            "max_pages": 3,
            "date_format": "MM/YYYY or DD Mon YYYY",
            "photo_required": False,
            "layout": "Skill-focused, dense but readable"
        },
        "sections": {
            "personal_info": ["linkedin", "github", "phone"],
            "education": "Very important, list top tier colleges",
            "skills": "Critical, place at top or side"
        },
        "strengths": [
            "Strong technical skill section",
            "Quantifiable achievements (ROI, % growth)",
            "Clear education credentials"
        ],
        "warnings": [
            "Photo is generally not needed",
            "Too generic, lacks specific tools/tech",
            "Summary is too vague"
        ],
        "keywords": ["Led", "Managed", "Developed", "Optimized", "Scaled"]
    },
    "USA": {
        "culture_context": "USA resumes must be concise, action-oriented, and achievement-focused. No photos, no age, no marital status (anti-discrimination).",
        "formatting": {
            "max_pages": 1,  # 2 for very senior
            "date_format": "MM/YYYY",
            "photo_required": False,
            "layout": "Clean, ATS-friendly, usually single column for easy parsing"
        },
        "sections": {
            "personal_info": ["city_state", "email", "linkedin"],
            "education": "Place after experience for seniors",
            "skills": "Embedded in experience or at bottom"
        },
        "strengths": [
            "Strong action verbs (Achieved, Created)",
            "Quantifiable metrics ($ saved, % increased)",
            "Concise and scans well (1 page ideal)"
        ],
        "warnings": [
            "Includes photo (Remove immediately)",
            "Includes personal details like age/marital status",
            "Too long (aim for 1 page)"
        ],
        "keywords": ["Revenue", "Growth", "Leadership", "Strategy"]
    },
    "Japan": {
        "culture_context": "Japan is very formal. The 'Rirekisho' is the standard format, though 'Shokumu Keirekisho' (CV) is used for detailed experience. Respect, harmony, and detail are key.",
        "formatting": {
            "max_pages": 2,
            "date_format": "YYYY/MM/DD",
            "photo_required": True,
            "layout": "Strictly chronological, formal templates"
        },
        "sections": {
            "personal_info": ["gender", "dob", "address"],
            "education": "Chronological history is vital",
            "experience": "Detailed project history"
        },
        "strengths": [
            "Follows standard Japanese formatting (JIS)",
            "Polite / Humble language (Keigo)",
            "Professional photo included"
        ],
        "warnings": [
            "Missing photo",
            "Too casual or aggressive tone",
            "Gaps in timeline (should be explained)"
        ],
        "keywords": ["Kaizen", "Teamwork", "Dedication", "Responsibility"]
    }
}

def get_country_context(country: str) -> str:
    """
    Returns a prompt-friendly string describing expectations for this country.
    """
    # Default to USA/Global if not found
    rules = COUNTRY_RULES.get(country, COUNTRY_RULES["USA"])
    
    sections_str = ", ".join([f"{k}: {v}" for k, v in rules["sections"].items()])
    
    context = (
        f"TARGET COUNTRY: {country}\n"
        f"CULTURAL NORMS: {rules['culture_context']}\n"
        f"FORMATTING: "
        f"Max Pages: {rules['formatting']['max_pages']}, "
        f"Photo: {'Required' if rules['formatting']['photo_required'] else 'No Photo'}, "
        f"Date Format: {rules['formatting']['date_format']}.\n"
        f"KEY SECTIONS: {sections_str}.\n"
        f"CRITICAL: You must penalize the score if these norms are violated."
    )
    return context

def get_country_fallback_data(country: str) -> dict:
    """
    Returns the static rules for a country to use when AI fails.
    """
    rules = COUNTRY_RULES.get(country, COUNTRY_RULES["USA"])
    return {
        "strengths": rules["strengths"],
        "warnings": rules["warnings"],
        "countrySpecific": [
            f"Expected Layout: {rules['formatting']['layout']}",
            f"Photo Policy: {'Must include professional photo' if rules['formatting']['photo_required'] else 'Do not include photo'}",
            f"Date Format: {rules['formatting']['date_format']}"
        ]
    }
