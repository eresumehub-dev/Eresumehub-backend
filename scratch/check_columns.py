import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the current directory to sys.path so we can import modules from backend
sys.path.append(os.getcwd())

from services.supabase_service import supabase_service

async def main():
    load_dotenv()
    try:
        # Initialize Supabase service if needed
        # It usually initializes itself from environment variables
        
        # Query user_profiles
        res = await supabase_service.client.table('user_profiles').select('*').limit(1).execute()
        if res.data:
            print(f"Columns: {list(res.data[0].keys())}")
        else:
            print("No data in user_profiles")
            
        # Query users
        res_u = await supabase_service.client.table('users').select('*').limit(1).execute()
        if res_u.data:
            print(f"User Columns: {list(res_u.data[0].keys())}")
        else:
            print("No data in users")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
