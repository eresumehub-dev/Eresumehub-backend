# utils/supabase_client.py

import os
import httpx
from supabase import create_client, Client, AsyncClient
from supabase.lib.client_options import AsyncClientOptions as ClientOptions
from dotenv import load_dotenv

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")

# Global HTTP client with HTTP/2 disabled to fix ConnectionTerminated errors on Windows
# We use a single shared client to avoid resource exhaustion
_httpx_client = httpx.AsyncClient(
    http1=True,
    http2=False, # This is the critical fix for Supabase + Windows
    timeout=httpx.Timeout(30.0),
    follow_redirects=True
)

# Global Supabase client instance
_supabase: AsyncClient = AsyncClient(
    SUPABASE_URL, 
    SUPABASE_SERVICE_KEY,
    options=ClientOptions(
        httpx_client=_httpx_client,
        storage=None # Uses default memory storage
    )
)

def get_client() -> AsyncClient:
    """Helper to get the global async supabase client"""
    return _supabase

# Export for convenience
supabase = _supabase

async def verify_connection():
    try:
        response = await _supabase.table('users').select('count', count='exact').limit(1).execute()
        return True, "Connected"
    except Exception as e:
        return False, str(e)

async def close_client():
    """Call this on shutdown to close the httpx pool"""
    await _httpx_client.aclose()

