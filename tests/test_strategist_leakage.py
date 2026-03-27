import asyncio
import logging
import sys
import os
import json

# Add backend directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.ai_service import AIService

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_strategist_leakage():
    print("\n" + "="*80)
    print("🧪  THE STRATEGIST: LEAKAGE & LOGIC TEST")
    print("="*80)

    ai_service = AIService()

    # 1. MOCK DATA: "Full-Stack Web Dev" applying for "AI Engineer"
    mock_user = {
        "full_name": "Test Candidate",
        "professional_summary": "I am a Full-Stack Developer with 5 years of experience in React and Node.js. I love building web apps.",
        "skills": ["React", "Node.js", "Python", "Automation", "AWS"],
        "work_experiences": [
            {
                "title": "Senior Web Developer",
                "company": "WebCorp",
                "description": "Built responsive websites using React. Created REST APIs with Node.js. Automated deployment scripts using Python."
            }
        ]
    }

    target_jd = "Looking for an AI Engineer to build RAG pipelines and Python automation agents. Must have strong backend skills."
    target_role = "AI Engineer"
    target_country = "Germany" # Tests RAG strictness (Date format: DD.MM.YYYY, Headers: Berufserfahrung)

    print(f"\n[INPUT] Role: {target_role} | Country: {target_country}")
    print(f"[INPUT] Original Identity: 'Full-Stack Developer' (Should be GHOSTED)")
    
    # 2. EXECUTE
    try:
        # We need to mock the RAG Service behavior effectively or ensure the files exist
        # Since I'm running in the backend context, it *should* pick up the real RAG files if they exist.
        
        result = await ai_service.generate_tailored_resume(
            mock_user, 
            target_jd, 
            target_country, 
            "English", 
            target_role
        )

        print("\n" + "="*30 + " GENERATED OUTPUT " + "="*30)
        print(json.dumps(result, indent=2))
        print("="*80)

        # 3. VERIFICATIONS
        summary = result.get("professional_summary", "").lower()
        experiences = result.get("work_experiences", [])
        exp_desc = ""
        if experiences:
            exp_desc = str(experiences[0].get("description", [])).lower()

        # TEST A: GHOSTING (Identity Protection)
        if "full-stack" in summary or "web developer" in summary:
            print("❌ FAILURE: 'Full-Stack' identity leaked into summary!");
        else:
            print("✅ SUCCESS: Old identity 'Full-Stack' successfully GHOSTED.")

        # TEST B: BRIDGE RULE ("Spin")
        if "automation" in summary or "start" in summary or "python" in summary: # Loose check for spun content
             print("✅ SUCCESS: Found target keywords (Python/Automation) in summary.")
        elif "automation" in exp_desc or "python" in exp_desc:
             print("✅ SUCCESS: Found target keywords in experience bullets.")
        else:
             print("⚠️ WARNING: AI might not have spun the content effectively. Check output.")

        # TEST C: RAG STRICTNESS
        # Check date format in experience
        if experiences:
            date_range = experiences[0].get("date_range", "")
            if "." in date_range: 
                print(f"✅ SUCCESS: Date format appears German (DD.MM.YYYY): {date_range}")
            else:
                print(f"❌ FAILURE: Date format is not German: {date_range}")
                
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_strategist_leakage())
