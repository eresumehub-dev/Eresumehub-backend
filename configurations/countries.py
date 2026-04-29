"""
Country-Specific Resume Rules & Configurations.
This file acts as a bridge to the RAG schema system, ensuring consistency 
across the AI prompt injection and fallback logic.
"""
import logging
from services.rag_service import RAGService

logger = logging.getLogger(__name__)

def get_country_context(country: str) -> str:
    """
    Returns a prompt-friendly string describing expectations for this country,
    sourced directly from the RAG knowledge base.
    """
    # Load from RAG (v16.5.6: Single Source of Truth)
    kb = RAGService.load_knowledge_base(country)
    
    # Fallback if country is completely missing (not just internal RAG default)
    if not kb or kb.get("country") == "United States" and country != "United States" and country != "USA":
        # Check if we should use USA as a global fallback
        kb = RAGService.load_knowledge_base("United States")
        
    cultural = kb.get("cultural_rules", {})
    ats = kb.get("ats_optimization", {})
    cv_struct = kb.get("cv_structure", {})
    
    # Format sections for the prompt
    sections = kb.get("required_sections", [])
    sections_str = ", ".join(sections) if isinstance(sections, list) else str(sections)
    
    context = (
        f"TARGET COUNTRY: {country}\n"
        f"TONE: {cultural.get('tone', 'Professional')}\n"
        f"CULTURAL NORMS: {cultural.get('context', 'Achievement-oriented and professional.')}\n"
        f"MAX PAGES: {cv_struct.get('max_pages', 2)}\n"
        f"DATE FORMAT: {cultural.get('date_format', 'MM/YYYY')}\n"
        f"PHOTO POLICY: {'Required' if ats.get('photo_policy') == 'Required' else 'No Photo'}\n"
        f"KEY SECTIONS: {sections_str}\n"
    )
    return context

def get_country_fallback_data(country: str) -> dict:
    """
    Returns the rules for a country to use when AI extraction or analysis fails.
    """
    kb = RAGService.load_knowledge_base(country)
    cultural = kb.get("cultural_rules", {})
    ats = kb.get("ats_optimization", {})
    
    return {
        "strengths": kb.get("strengths", ["Professional format"]),
        "warnings": kb.get("warnings", ["Ensure all sections are complete"]),
        "countrySpecific": [
            f"Expected Layout: {ats.get('layout_type', 'Standard')}",
            f"Photo Policy: {ats.get('photo_policy', 'Do not include photo')}",
            f"Date Format: {cultural.get('date_format', 'MM/YYYY')}"
        ]
    }
