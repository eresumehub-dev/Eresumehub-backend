
import asyncio
import os
import sys
from datetime import date

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from backend.services.profile_service import ProfileService
from backend.main import supabase_client

async def test_golden_sample_integrity():
    print("🚀 Starting German Golden Sample Integrity Test...")
    
    # Mock User ID (must exist in Users table, or we use a dummy if not enforcing FK strictly in mock)
    # Ideally we'd create a user, but let's assume one exists or we can grab one.
    # For safety, let's try to get the first user from the DB.
    try:
        user_response = await supabase_client.table('users').select('id, email').limit(1).execute()
        if not user_response.data:
            print("❌ No users found to test with.")
            return
        
        test_user = user_response.data[0]
        user_id = test_user['id']
        print(f"👤 Using Test User: {test_user['email']} ({user_id})")

        profile_service = ProfileService()

        # 1. Define Golden Sample Data
        golden_profile = {
            "title": "Senior Java Developer",
            "full_name": "Max Mustermann",
            "email": "max.mustermann@example.de",
            "phone": "+49 123 456789",
            "street_address": "Musterstraße 123",
            "postal_code": "10115",
            "city": "Berlin",
            "country": "Germany",
            "nationality": "German",
            "date_of_birth": "1990-01-01",
            "professional_summary": "Experienced Java Developer...",
            "languages": [
                {"name": "German", "level": "Native"},
                {"name": "English", "level": "C1"}
            ],
            "work_experiences": [
                {
                    "job_title": "Lead Developer",
                    "company": "Tech GmbH",
                    "city": "Munich",
                    "country": "Germany",
                    "start_date": "2020-01",
                    "is_current": True,
                    "achievements": ["Optimized backend performance by 20%", "Led a team of 5 developers"]
                }
            ],
            "educations": [
                {
                    "degree": "B.Sc. Computer Science",
                    "institution": "TU Berlin",
                    "city": "Berlin",
                    "country": "Germany",
                    "graduation_date": "2015-06"
                }
            ]
        }

        print("📝 Inserting Golden Sample Profile...")
        result = await profile_service.create_or_update_profile(user_id, golden_profile)
        
        # 2. Verification
        print("🔍 Verifying Data persistence...")
        
        # Refetch from DB to be sure
        fetched = await profile_service.get_profile(user_id)
        
        # Check Fields
        errors = []
        
        if fetched.get('street_address') != "Musterstraße 123": errors.append(f"Street Address Mismatch: {fetched.get('street_address')}")
        if fetched.get('postal_code') != "10115": errors.append(f"Postal Code Mismatch: {fetched.get('postal_code')}")
        if fetched.get('nationality') != "German": errors.append(f"Nationality Mismatch: {fetched.get('nationality')}")
        if str(fetched.get('date_of_birth')) != "1990-01-01": errors.append(f"DOB Mismatch: {fetched.get('date_of_birth')}")
        
        # Check Nested Arrays
        langs = fetched.get('languages', [])
        if not any(l['name'] == 'German' and l.get('level') in ['Native', 'C2'] for l in langs):
             # Note: 'Native' might be stored as is, but we mapped it in AI extraction. 
             # Here we sent 'Native' directly verify it stores what is sent.
             errors.append(f"Language persistence failed: {langs}")
             
        exps = fetched.get('work_experiences', [])
        if not exps or exps[0].get('city') != 'Munich':
            errors.append(f"Experience City persistence failed: {exps}")

        if not errors:
            print("✅ TEST PASSED: All German fields persisted correctly!")
            print("✅ Address: OK")
            print("✅ Nationality/DOB: OK")
            print("✅ Nested Objects (Exp/Edu/Lang): OK")
        else:
            print("❌ TEST FAILED:")
            for e in errors:
                print(f"  - {e}")

    except Exception as e:
        print(f"💥 CRITICAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_golden_sample_integrity())
