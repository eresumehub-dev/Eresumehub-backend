import asyncio
import argparse
import sys
import os

# Add parent directory to path to import backend modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.supabase_service import supabase_service
from app_settings import Config

async def rescue_account(email: str):
    """
    Manually unify identity data for a specific user email.
    Useful when a Google/Social re-login orphans profile/resume data.
    """
    print(f"🚀 Initializing Rescue for: {email}")
    
    client = supabase_service.client
    if not client:
        from utils.supabase_client import get_client
        client = get_client()
        
    # 1. Resolve Current Active Identity from public.users
    user_resp = await client.table("users").select("*").eq("email", email).limit(1).execute()
    if not user_resp.data:
        print(f"❌ Error: No record found for email {email} in public.users table.")
        return

    user_row = user_resp.data[0]
    current_auth_id = user_row.get("auth_user_id")
    internal_id = user_row.get("id")
    
    print(f"✅ Found user: {user_row.get('full_name')} (Internal ID: {internal_id})")
    print(f"📡 Current Active Auth ID: {current_auth_id}")
    
    # 2. Find Orphaned Data (Records not matching current_auth_id but tied to internal_user_id if available,
    # or just records you know belong to this person but have a different user_id).
    # Since we don't have a history of old auth IDs, we can't easily query by 'old_id' unless provided.
    # However, in Eresumehub, most tables use 'user_id' which IS the auth_user_id.
    
    # If the user has data that represents them but has a different ID, we need THAT ID.
    # We can try to find it by looking for profiles with the same email in user_profiles if stored there.
    
    profile_resp = await client.table("user_profiles").select("*").eq("email", email).execute()
    orphaned_ids = []
    for p in profile_resp.data:
        p_id = p.get("user_id")
        if p_id != current_auth_id:
            orphaned_ids.append(p_id)
            
    if not orphaned_ids:
        # Fallback: Check for resumes that might be orphaned if we can't find them via profiles
        print("❓ No orphaned profiles found by email. Searching for any orphaned records...")
        # (This part is harder without knowing the old UUID).
    else:
        print(f"🔍 Found {len(orphaned_ids)} orphaned identity signatures: {orphaned_ids}")
        
    for old_id in orphaned_ids:
        print(f"🔄 Migrating data from {old_id} -> {current_auth_id}...")
        
        tables = [
            ("user_profiles", "user_id"),
            ("resumes", "user_id"),
            ("user_analytics_cache", "user_id"),
            ("resume_views", "viewer_user_id"),
            ("resume_likes", "user_id")
        ]
        
        for table, col in tables:
            try:
                resp = await client.table(table).update({col: current_auth_id}).eq(col, old_id).execute()
                print(f"   - {table}: Updated {len(resp.data) if resp.data else 0} rows")
            except Exception as e:
                print(f"   - {table}: Failed update - {e}")

    print("\n✨ Rescue mission complete. Please refresh your dashboard.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rescue orphaned Eresumehub account data")
    parser.add_argument("--email", required=True, help="User email to rescue")
    args = parser.parse_args()
    
    asyncio.run(rescue_account(args.email))
