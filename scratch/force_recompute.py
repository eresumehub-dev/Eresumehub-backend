import asyncio
import os
from services.analytics_service import AnalyticsService
from services.supabase_service import supabase_service

async def force_recompute():
    user_id = "fce6f5be-eef4-4050-a9b0-0bf86f6488f5"
    print(f"Forcing recompute for user: {user_id}")
    svc = AnalyticsService(supabase_service)
    result = await svc.refresh_user_analytics_cache(user_id)
    print(f"Recompute finished. Performance entries: {len(result.get('resume_performance', []))}")
    for p in result.get('resume_performance', []):
        print(f" - {p.get('title')}: Views={p.get('views')}, Downloads={p.get('downloads')}")

if __name__ == "__main__":
    asyncio.run(force_recompute())
