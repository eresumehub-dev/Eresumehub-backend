import os
import requests
import json
from dotenv import load_dotenv

# Load env from .env file
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "").strip('"').strip("'")
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

def test_list_models():
    if not API_KEY:
        print("ERROR: No GEMINI_API_KEY found in .env")
        return

    url = f"{BASE_URL}/models?key={API_KEY}"
    print(f"Testing connectivity to: {BASE_URL}/models")
    
    try:
        response = requests.get(url)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\nAvailable Models:")
            for m in data.get('models', []):
                print(f" - {m['name']}")
        else:
            print(f"Error Response: {response.text}")
            
    except Exception as e:
        print(f"Exception: {e}")

def test_generate_content(model_name="models/gemini-1.5-flash"):
    print(f"\nTesting generation with {model_name}...")
    url = f"{BASE_URL}/{model_name}:generateContent?key={API_KEY}"
    
    payload = {
        "contents": [{"parts": [{"text": "Hello, explain 404 error."}]}]
    }
    
    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Success!")
        else:
            print(f"Failed: {response.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_list_models()
    # test_generate_content("models/gemini-1.5-flash")
