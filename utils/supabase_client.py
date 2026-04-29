# utils/supabase_client.py

import os
import httpx
from typing import Optional
from supabase import create_client, Client, AsyncClient
from supabase.lib.client_options import AsyncClientOptions as ClientOptions
from dotenv import load_dotenv

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")

# Global Supabase client singleton (v16.5.3 Lazy Init)
_supabase: Optional[AsyncClient] = None
_httpx_client: Optional[httpx.AsyncClient] = None

def get_client() -> AsyncClient:
    """
    Helper to get the global async supabase client (Audit A3: Lazy Init).
    """
    global _supabase, _httpx_client
    
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
             raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")

        # Initialize HTTPX pool only on first use
        _httpx_client = httpx.AsyncClient(
            http1=True,
            http2=False, # Critical fix for Supabase + Windows
            timeout=httpx.Timeout(45.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        
        # Initialize Supabase client
        _supabase = AsyncClient(
            SUPABASE_URL, 
            SUPABASE_SERVICE_KEY,
            options=ClientOptions(
                httpx_client=_httpx_client,
                storage=None
            )
        )
        logger.info("Supabase Client: Initialized lazily (Audit A3)")
        
    return _supabase

# Export for convenience (v16.4.15 backward compatibility)
# Note: Use get_client() for true lazy behavior
supabase = None # Will be populated by property/accessor or kept as None

async def verify_connection():
    try:
        response = await _supabase.table('users').select('count', count='exact').limit(1).execute()
        return True, "Connected"
    except Exception as e:
        return False, str(e)

async def close_client():
    """Call this on shutdown to close the httpx pool"""
    await _httpx_client.aclose()

