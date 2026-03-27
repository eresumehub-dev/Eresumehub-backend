import asyncio
import json
import re
from services.ai_service import ai_service
from services.resume_autocorrect import resume_autocorrect
from utils.resume_validator import ResumeComplianceValidator

async def test_japan_compliance():
    print("🚀 Starting Japan Resume Compliance Verification...")
    
    # Mock user data that previously failed
    mock_user_data = {
        "full_name": "Emanuel V.",
        "professional_summary": "I am a seasoned Senior Creative Designer with professional experience. I led teams.",
        "languages": [
            {"name": "English", "level": "C2"},
            {"name": "Japanese", "level": "Native"},
            {"name": "French", "level": "A2"}
        ],
        "work_experiences": [
            {"company": "PrintVogue", "job_title": "Lead Designer", "start_date": "2019-01-01", "description": ["I managed a team."]},
        ],
        "certifications": [{"name": "JLPT", "issue_date": "2021-12-01"}],
        "date_of_birth": "1992-03-12",
        "nationality": "Indian",
        "profile_pic_url": "https://i.imgur.com/GzOqf1R.jpeg"
    }
    
    # 1. Test Auto-Correction
    print("\n🔍 Testing Layer 1: Auto-Correction...")
    corrected_data = resume_autocorrect.autocorrect_for_country(mock_user_data, "Japan")
    
    # Verify English level mapping
    eng_lang = next(l for l in corrected_data["languages"] if l["name"] == "English")
    print(f"   [CHECK] English Level: {eng_lang['level']}")
    assert "TOEIC" in eng_lang["level"]
    
    # Verify Pronoun removal
    print(f"   [CHECK] Pronouns in Summary: {'I ' not in corrected_data['professional_summary']}")
    assert "I " not in corrected_data["professional_summary"]
    assert "am " not in corrected_data["professional_summary"] # Fixed "I am" -> "Experienced"
    
    # 2. Test Validator (Simulate AI adding blocks)
    print("\n🔍 Testing Layer 2: Compliance Validator...")
    # Add Japanese mandatory blocks for validation
    corrected_data["self_pr"] = "Extensive experience in fashion."
    corrected_data["motivation"] = "Interest in Japanese craftsmanship."
    
    validation_result = ResumeComplianceValidator.validate(corrected_data, "Japan")
    print(f"   [CHECK] Validation Result: {'VALID' if validation_result['valid'] else 'INVALID'}")
    if not validation_result['valid']:
        print(f"   [ERRORS] {validation_result['errors']}")
    
    assert validation_result['valid'] == True
    
    print("\n✅ Verification PASSED: 100/100 Compliance Score Achievable.")

if __name__ == "__main__":
    asyncio.run(test_japan_compliance())
