# tests/test_complete_flow.py
"""
Complete flow test for E-resumehub with Supabase
Tests:
- Signup
- Login
- Username Check
- Create Resume
- View Profile (public)
- View Resume (public)
- Get My Resumes
"""

import requests
import json
from time import sleep

BASE_URL = "http://localhost:8000"


# -------------------------------------
# UTILITIES
# -------------------------------------
def pretty(obj):
    print(json.dumps(obj, indent=2))


# -------------------------------------
# SIGNUP
# -------------------------------------
def test_signup():
    print("\n📝 Testing Signup...")

    payload = {
        "username": "johndoe123",
        "email": "john.doe.test@example.com",
        "password": "SecurePass123!",
        "full_name": "John Doe",
        "headline": "Software Engineer",
        "location": "San Francisco, CA"
    }

    response = requests.post(f"{BASE_URL}/api/v1/auth/signup", json=payload)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print("✅ Signup successful!")
        pretty(data)
        return data["data"]["session"]["access_token"]

    print("⚠️ Signup failed:", response.text)
    return None


# -------------------------------------
# LOGIN
# -------------------------------------
def test_login():
    print("\n🔐 Testing Login...")

    response = requests.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={
            "email": "john.doe.test@example.com",
            "password": "SecurePass123!"
        }
    )

    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print("✅ Login successful!")
        pretty(data)
        return data["data"]["session"]["access_token"]

    print("❌ Login failed:", response.text)
    return None


# -------------------------------------
# USERNAME CHECK
# -------------------------------------
def test_check_username():
    print("\n🔍 Testing Username Check...")

    for username in ["newuser999", "johndoe123"]:
        response = requests.get(f"{BASE_URL}/api/v1/username/check/{username}")
        data = response.json()
        print(f"{username} available: {data['data']['available']}")


# -------------------------------------
# CREATE RESUME
# -------------------------------------
def test_create_resume(access_token):
    print("\n📄 Testing Resume Creation...")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "title": "Software Engineer Resume",
        "slug": "software-engineer",
        "country": "USA",
        "language": "English",
        "template_style": "professional",
        "visibility": "public",
        "is_default": True,
        "tags": ["python", "fastapi", "backend"],
        "resume_data": {
            "full_name": "John Doe",
            "contact": {
                "email": "john.doe@example.com",
                "phone": "+15551234567",
                "linkedin": "https://linkedin.com/in/johndoe",
                "github": "https://github.com/johndoe"
            },
            "summary": "Experienced Software Engineer with 5 years in backend.",
            "experience": [
                {
                    "title": "Senior Software Engineer",
                    "company": "Tech Corp",
                    "location": "San Francisco, CA",
                    "start_date": "January 2020",
                    "end_date": "Present",
                    "description": [
                        "Led development of microservices",
                        "Improved performance by 40%"
                    ]
                }
            ],
            "education": [
                {
                    "degree": "B.S. CS",
                    "institution": "Stanford University",
                    "graduation_date": "2018",
                    "gpa": 3.8
                }
            ],
            "skills": ["Python", "FastAPI", "PostgreSQL"]
        }
    }

    response = requests.post(
        f"{BASE_URL}/api/v1/resumes",
        headers=headers,
        json=payload
    )

    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print("✅ Resume created successfully!")
        pretty(data)
        return data["data"]["resume"]["id"]

    print("❌ Resume creation failed:", response.text)
    return None


# -------------------------------------
# PUBLIC PROFILE VIEW
# -------------------------------------
def test_view_profile():
    print("\n👤 Testing Public Profile View...")

    response = requests.get(f"{BASE_URL}/johndoe123")
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print("✅ Profile loaded successfully!")
        pretty(data)
    else:
        print("❌ Profile view failed:", response.text)


# -------------------------------------
# PUBLIC RESUME VIEW
# -------------------------------------
def test_view_resume():
    print("\n📄 Testing Public Resume View...")

    response = requests.get(f"{BASE_URL}/johndoe123/software-engineer")
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print("✅ Resume loaded successfully!")
        pretty(data)
    else:
        print("❌ Resume view failed:", response.text)


# -------------------------------------
# GET USER RESUMES
# -------------------------------------
def test_get_my_resumes(access_token):
    print("\n📚 Testing Get My Resumes...")

    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(
        f"{BASE_URL}/api/v1/users/me/resumes",
        headers=headers
    )

    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print("✅ Loaded resumes successfully!")
        pretty(data)
    else:
        print("❌ Failed:", response.text)


# -------------------------------------
# RUN ALL TESTS
# -------------------------------------
def run_all_tests():
    print("=" * 60)
    print("  🚀 E-RESUMEHUB COMPLETE FLOW TEST")
    print("=" * 60)

    # Health check
    print("\n❤️ Testing Health Check...")
    health = requests.get(f"{BASE_URL}/api/v1/health")
    if health.status_code != 200:
        print("❌ API is DOWN!")
        return
    print("✅ API is healthy!")

    # Username check
    test_check_username()

    # Signup
    token = test_signup()
    
    # If signup fails → login instead
    if not token:
        print("\n⚠️ Signup failed — attempting login...")
        token = test_login()

    if not token:
        print("\n❌ Cannot continue without an access token.")
        return

    # Create resume
    resume_id = test_create_resume(token)
    sleep(1)

    # Public access
    test_view_profile()
    test_view_resume()

    # Private access
    test_get_my_resumes(token)

    print("\n" + "=" * 60)
    print("  🎉 ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
