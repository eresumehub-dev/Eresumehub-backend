import asyncio
import os
import sys
from utils.supabase_client import get_client

async def check():
    try:
        sb = get_client()
        res = await sb.table('endpoint_latency_logs').select('*').order('created_at', desc=True).limit(5).execute()
        print("--- LAST 5 LATENCY LOGS ---")
        for log in res.data:
            print(f"Path: {log.get('path')} | Status: {log.get('status_code')} | User: {log.get('user_id')} | Created: {log.get('created_at')}")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(check())
