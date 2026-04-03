import asyncio
import logging
import sys
import os

# Set up logging to show resolution events
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add current directory to path
sys.path.append(os.getcwd())

async def verify_identity_resolution():
    from services.supabase_service import supabase_service
    from services.profile_service import ProfileService
    
    ps = ProfileService(supabase_service)
    
    # 1. Get a random user with both IDs
    res = await supabase_service.client.table('users').select('id, auth_user_id').limit(1).execute()
    if not res.data:
        print("No users found in database to test with.")
        return

    user = res.data[0]
    platform_id = user['id']
    auth_id = user['auth_user_id']
    
    print(f"--- Identity Resolution Test ---")
    print(f"Input Platform ID: {platform_id}")
    print(f"Target Auth ID:     {auth_id}")
    
    # 2. Test manual resolution
    resolved = await ps._resolve_auth_id(platform_id)
    print(f"Resolved Result:    {resolved}")
    
    if resolved == auth_id:
        print("✅ SUCCESS: Platform ID correctly resolved to Auth ID.")
    else:
        print("❌ FAILURE: Resolution mismatch.")

    # 3. Test Header Fetch (The 'Fast Path' that was failing)
    header = await ps.get_profile_header(platform_id)
    if header:
        print(f"✅ SUCCESS: Profile Header found using Platform ID.")
    else:
        print(f"❌ FAILURE: Profile Header not found.")

if __name__ == "__main__":
    asyncio.run(verify_identity_resolution())
