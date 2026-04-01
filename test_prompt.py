# test_prompt.py
from services.ai_service import AIService
import asyncio
import json

async def run_test():
    prompt = AIService._build_prompt("test", "Germany", "English", "modern", {'full_name': 'Test'}, "dev")
    print(prompt)

if __name__ == "__main__":
    asyncio.run(run_test())
