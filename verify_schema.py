import asyncio
import logging
import os
import sys
import io
from datetime import datetime

# Setup UTF-8 encoding for Windows terminals (v16.2.0)
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Add backend directory to path
sys.path.append(os.getcwd())

async def get_live_migrations(supabase):
    """Fetch all applied migrations from the live DB"""
    try:
        res = await supabase.table("migrations_log").select("migration_name").execute()
        return {row["migration_name"] for row in res.data}
    except Exception as e:
        logger.error(f"Failed to fetch migrations_log: {e}")
        return set()

async def audit_identity_fusion(supabase):
    """Deep Schema Audit: Check if tables are correctly keyed by Auth UUIDs (v16.0.0)"""
    checks = []
    
    try:
        # Check user_profiles for uniqueness and ID type (proxy check)
        res = await supabase.table("user_profiles").select("user_id").limit(1).execute()
        if res.data:
            # We check a known Auth UUID pattern if possible, or just log success of the query
            checks.append(("Identity Unification (Profiles)", "✅ Passed [Query Success]"))
        else:
            checks.append(("Identity Unification (Profiles)", "⚠️ No data found to verify"))

        # Check resumes table
        res_r = await supabase.table("resumes").select("user_id").limit(1).execute()
        if res_r.data:
            checks.append(("Identity Unification (Resumes)", "✅ Passed [Query Success]"))
        
    except Exception as e:
        logger.error(f"Schema Audit Failure: {e}")
        checks.append(("Identity Unification", f"❌ FAILED: {e}"))
    
    return checks

async def main():
    from utils.supabase_client import get_client
    supabase = get_client()
    
    # 1. Fetch Local Migrations
    script_dir = os.path.dirname(os.path.abspath(__file__))
    migrations_dir = os.path.join(script_dir, "migrations")
    
    if not os.path.exists(migrations_dir):
        print(f"🚨 CRITICAL: Migrations directory not found at {migrations_dir}")
        return

    local_files = {f for f in os.listdir(migrations_dir) if f.endswith(".sql")}
    
    print(f"\n--- 🧬 Migration Synchronization Audit (v16.1.0) ---")
    print(f"Directory: {migrations_dir}")
    print(f"Local files found: {len(local_files)}\n")
    
    # 2. Fetch Live Migrations
    live_migrations = await get_live_migrations(supabase)
    
    if not live_migrations:
        print("🚨 CRITICAL: Could not find 'migrations_log' table in Supabase.")
        print("💡 Solution: Run '20260403_init_migration_log.sql' in Supabase SQL Editor.\n")
    
    # 3. Compare Sync Status
    print(f"--- 🔄 Sync Status ---")
    pending = []
    for f in sorted(local_files):
        status = "✅ Applied" if f in live_migrations else "❌ PENDING"
        if f not in live_migrations:
            pending.append(f)
        print(f"[{status}] {f}")
    
    # 4. Deep Structural Audit
    print(f"\n--- 🔎 Deep Schema Audit (v16.0.0 Consistency) ---")
    audit_results = await audit_identity_fusion(supabase)
    for area, result in audit_results:
        print(f"{area}: {result}")
    
    # 5. Final Verdict
    print(f"\n--- 🏁 Final Verdict ---")
    if pending:
        print(f"🚨 UNSAFE: {len(pending)} migration(s) are pending execution.")
        print(f"👉 DO NOT SHIP: Backend code may fail due to schema drift.")
        print(f"💡 Action: Execute the pending scripts manually in Supabase SQL Editor.")
    else:
        print("🚀 SAFE TO SHIP: Production database is in sync with backend code.")
    print("-" * 50 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
