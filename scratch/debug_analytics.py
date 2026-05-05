import asyncio
import os
from services.supabase_service import supabase_service

async def check():
    user_id = "fce6f5be-eef4-4050-a9b0-0bf86f6488f5"
    print(f"Checking resumes for user: {user_id}")
    resumes = await supabase_service.get_user_resumes(user_id)
    print(f"Found {len(resumes)} active resumes.")
    for r in resumes:
        print(f" - {r['id']}: {r['title']} (Deleted At: {r.get('deleted_at')})")
    
    print("\nChecking ALL resumes (including deleted) to see if filter is working...")
    all_resumes = await supabase_service.client.table("resumes").select("id, title, deleted_at").eq("user_id", user_id).execute()
    print(f"Found {len(all_resumes.data)} total resumes in DB.")
    for r in all_resumes.data:
        print(f" - {r['id']}: {r['title']} (Deleted At: {r.get('deleted_at')})")

    print("\nChecking Analytics Cache...")
    cache = await supabase_service.client.table("user_analytics_cache").select("*").eq("user_id", user_id).execute()
    if cache.data:
        print(f"Cache record exists. Updated At: {cache.data[0].get('updated_at')}")
        perf = cache.data[0].get('dashboard_json', {}).get('resume_performance', [])
        print(f"Cache contains {len(perf)} resumes in performance list.")
        for p in perf:
            print(f" - {p.get('title')}")
    else:
        print("No cache record found.")

if __name__ == "__main__":
    asyncio.run(check())
