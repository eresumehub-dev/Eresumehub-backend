import asyncio
import os
import sys

# Add backend to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.supabase_service import supabase_service
from services.profile_service import ProfileService

async def verify_parallel_profile():
    profile_service = ProfileService(supabase_service)
    
    # Use a known test user ID or the first profile found
    profiles = await supabase_service.client.table('user_profiles').select('user_id').limit(1).execute()
    if not profiles.data:
        print("No profiles found to verify.")
        return

    user_id = profiles.data[0]['user_id']
    print(f"Verifying profile fetch for user: {user_id}")
    
    import time
    start = time.time()
    profile = await profile_service.get_profile(user_id)
    elapsed = time.time() - start
    
    print(f"Profile fetched in {elapsed:.4f}s")
    
    # Check structure
    expected_keys = ['work_experiences', 'educations', 'projects', 'certifications', 'extras']
    for key in expected_keys:
        if key in profile:
            print(f"[OK] Found {key}: {len(profile[key]) if isinstance(profile[key], list) else 'dict'}")
        else:
            print(f"[FAIL] Missing {key}")

if __name__ == "__main__":
    asyncio.run(verify_parallel_profile())
