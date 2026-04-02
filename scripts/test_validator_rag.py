import sys
import os

# Adjust path to import backend modules
# scripts/ is in root, backend/ is in root. So we need to go up one level from scripts, then into backend?
# Wait, if I append root (Eresumehub), then I can import backend.utils... if backend is a package?
# Or if I append backend/ directly, I import utils...
# Original structure: backend/utils/resume_validator.py
# So if I append 'backend', I can do 'from utils...'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../backend')))

from utils.resume_validator import ResumeComplianceValidator

def test_validator():
    print("Testing ResumeComplianceValidator with RAG Rules...\n")

    # 1. Test Case: German Resume (Missing German)
    user_data_no_german = {
        "contact": {"city": "Munich"},
        "education": [{"institution": "TUM", "degree": "MSc"}],
        "languages": ["English"]
    }
    print("1. Testing Germany (Missing German)...")
    res = ResumeComplianceValidator.validate(user_data_no_german, "Germany")
    print(f"   Result: {res['valid']} (Expected: False)")
    if not res['valid']:
        print(f"   Errors: {[e['message'] for e in res['errors']]}")
    else:
        print("   FAILED: Should have failed due to missing German.")
    print("-" * 30)

    # 2. Test Case: German Resume (Valid)
    user_data_german = {
        "contact": {"city": "Munich"},
        "education": [{"institution": "TUM", "degree": "MSc"}],
        "languages": ["English", "German (Native)"]
    }
    print("2. Testing Germany (Valid)...")
    res = ResumeComplianceValidator.validate(user_data_german, "Germany")
    print(f"   Result: {res['valid']} (Expected: True)")
    if not res['valid']:
        print(f"   Errors: {res['errors']}")
    print("-" * 30)

    # 3. Test Case: USA (No Knowledge Base -> Should Pass)
    print("3. Testing USA (No Rules)...")
    res = ResumeComplianceValidator.validate(user_data_no_german, "United States")
    print(f"   Result: {res['valid']} (Expected: True)")
    print("-" * 30)
    
    # 4. Test Case: India (Missing English)
    print("4. Testing India (Missing English)...")
    user_data_no_english = {
         "contact": {"city": "Mumbai"}, # Mandatory for India (Header)
         "education": [{"institution": "IIT", "degree": "BTech"}],
         "languages": ["Hindi"]
    }
    res = ResumeComplianceValidator.validate(user_data_no_english, "India")
    print(f"   Result: {res['valid']} (Expected: False)")
    if not res['valid']:
        print(f"   Errors: {[e['message'] for e in res['errors']]}")

if __name__ == "__main__":
    # Redirect stdout to a file for reliable debugging
    with open("validation_debug.log", "w", encoding="utf-8") as f:
        sys.stdout = f
        try:
            test_validator()
        except Exception as e:
            print(f"CRASH: {e}")
        finally:
            sys.stdout = sys.__stdout__  # Restore
    
    # Print success message to console
    print("Test complete. Check validation_debug.log")
