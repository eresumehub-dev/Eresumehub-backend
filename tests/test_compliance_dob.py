import sys
import os
import json

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from utils.resume_validator import ResumeComplianceValidator

def test_german_compliance_no_dob():
    print("Testing German Compliance (Missing DOB)...")
    
    # Mock User Data (Missing DOB)
    user_data = {
        "full_name": "Test User",
        "email": "test@example.com",
        "phone": "+49 123 45678",
        "city": "Berlin",
        "nationality": "German", # Present
        # "date_of_birth": None, # MISSING
        "educations": [{"degree": "B.Sc"}],
        "projects": [{"title": "Project A"}],
        "languages": ["German", "English"]
    }
    
    result = ResumeComplianceValidator.validate(user_data, country="Germany")
    
    print(f"Valid: {result['valid']}")
    print("Errors:")
    for err in result['errors']:
        print(f" - {err['message']} (Code: {err['code']})")
        
    # Check if we got the specific error
    has_dob_error = any(e['code'] == 'MISSING_DOB' for e in result['errors'])
    if has_dob_error:
        print("\nSUCCESS: Detected missing DOB.")
    else:
        print("\nFAILURE: Did not detect missing DOB.")

if __name__ == "__main__":
    test_german_compliance_no_dob()
