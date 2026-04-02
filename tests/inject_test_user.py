import asyncio
import os
import sys

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from services.profile_service import ProfileService
from services.supabase_service import SupabaseService
from utils.supabase_client import supabase as supabase_client

async def inject_test_user():
    print("🚀 Injecting Target Profile for Verification Test...")
    
    try:
        user_response = await supabase_client.table('users').select('id, email').limit(1).execute()
        if not user_response.data:
            print("❌ No users found to test with.")
            return
        
        test_user = user_response.data[0]
        user_id = test_user['id']
        print(f"👤 Using Test User: {test_user['email']} ({user_id})")

        supabase_service = SupabaseService()
        profile_service = ProfileService(supabase_service)

        # Define the exact problematic profile
        target_profile = {
            "title": "Senior Solutions Architect",
            "full_name": "Kenji Sato",
            "email": "kenji.sato@example.co.jp",
            "phone": "+81 90 1234 5678",
            "street_address": "1-2-3 Shibuya",
            "postal_code": "150-0002",
            "city": "Tokyo",
            "country": "Japan",
            "nationality": "Japanese",
            "date_of_birth": "1990-05-15", # Setting it exactly to see if template fixes it
            "professional_summary": "", # Empty, forcing AI to generate it
            "languages": [
                {"name": "Japanese", "level": "Native"},
                {"name": "English", "level": "Business (TOEIC 900)"}
            ],
            "skills": [
                "AWS", "Python", "Kubernetes", "DevOps", "Cloud Computing"
            ],
            "work_experiences": [
                {
                    "job_title": "Lead Cloud Architect",
                    "company": "Nippon Tech Solutions",
                    "city": "Tokyo",
                    "country": "Japan",
                    "start_date": "2018-04-01",
                    "end_date": "Present",
                    "is_current": True,
                    "achievements": [
                        "I managed a team of 10 engineers to migrate local instances to AWS.",
                        "We created an automated CI/CD pipeline using Jenkins and Docker.",
                        "I improved system uptime by 99.99%."
                    ]
                },
                {
                    "job_title": "Backend Software Engineer",
                    "company": "Tokyo Startup Inc.",
                    "city": "Tokyo",
                    "country": "Japan",
                    "start_date": "2013-04-01",
                    "end_date": "2018-03-31",
                    "is_current": False,
                    "achievements": [
                        "Developed REST APIs using Python and Django.",
                        "Worked on database optimization reducing query times by 30%."
                    ]
                }
            ],
            "educations": [
                {
                    "degree": "B.Sc. Computer Engineering",
                    "institution": "University of Tokyo",
                    "city": "Tokyo",
                    "country": "Japan",
                    "graduation_date": "2013-03-31"
                }
            ]
        }

        print("📝 Injecting Profile...")
        await profile_service.create_or_update_profile(user_id, target_profile)
        print("✅ SUCCESS! Profile data injected. You can now refresh the app and run the generation test.")

    except Exception as e:
        print(f"💥 CRITICAL ERROR: {str(e)}")

if __name__ == "__main__":
    asyncio.run(inject_test_user())
