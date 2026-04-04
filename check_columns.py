import asyncio
from utils.supabase_client import get_client

async def check():
    try:
        sb = get_client()
        # Fetch one row to see columns
        res = await sb.table("resumes").select("*").limit(1).execute()
        if res.data:
            print(f"COLUMNS IN RESUMES: {list(res.data[0].keys())}")
        else:
            print("No resumes found in database to check columns.")
            # Try a dummy insert with introspection if needed, or query information_schema
            query = "SELECT column_name FROM information_schema.columns WHERE table_name = 'resumes'"
            # Note: rpc is safer for raw SQL
            res = await sb.rpc("get_table_columns", {"table_name": "resumes"}).execute()
            print(f"COLUMNS (via RPC): {res.data}")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(check())
