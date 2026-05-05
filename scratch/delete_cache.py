import asyncio
import os
from services.supabase_service import supabase_service

async def d():
    user_id = "fce6f5be-eef4-4050-a9b0-0bf86f6488f5"
    print(f"Deleting cache for {user_id}")
    r = await supabase_service.client.table('user_analytics_cache').delete().eq('user_id', user_id).execute()
    print(f"Result: {r}")

if __name__ == "__main__":
    asyncio.run(d())
