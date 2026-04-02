
import os
import sys
import subprocess

def check_schema():
    print("🔍 Checking Database Schema for 'city' column in 'work_experiences'...")
    
    # Use psql directly via subprocess to avoid dependency issues
    # We want to check if the column exists.
    query = "SELECT column_name FROM information_schema.columns WHERE table_name = 'work_experiences' AND column_name = 'city';"
    
    try:
        # Run psql command
        # Assumes 'psql' is in PATH and user/db matches
        cmd = ['psql', '-h', 'localhost', '-U', 'postgres', '-d', 'erresumehub', '-t', '-c', query]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"❌ Error running psql: {result.stderr}")
            return
            
        output = result.stdout.strip()
        print(f"Output: '{output}'")
        
        if 'city' in output:
            print("✅ SUCCESS: Column 'city' EXISTS in 'work_experiences'.")
            print("👉 Diagnosis: The column exists in DB, but PostgREST schema cache is stale.")
            print("👉 Action: Restart Supabase/PostgREST or force schema reload.")
        else:
            print("❌ FAILURE: Column 'city' MISSING from 'work_experiences'.")
            print("👉 Diagnosis: The migration did not run successfully.")
            print("👉 Action: Re-run the migration SQL.")
            
    except Exception as e:
        print(f"💥 Exception: {e}")

if __name__ == "__main__":
    check_schema()
