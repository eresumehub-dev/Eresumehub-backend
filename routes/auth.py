# routers/auth.py

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
import logging
from utils.supabase_client import supabase
from services.supabase_service import supabase_service

logger = logging.getLogger(__name__)

router = APIRouter()

# -----------------------------
# REQUEST MODELS
# -----------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    username: Optional[str] = None
    full_name: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# -----------------------------
# SIGNUP
# -----------------------------
@router.post("/signup")
async def signup(body: SignupRequest):
    try:
        # Generate username if not provided
        username = body.username
        if not username:
            import uuid
            # Generate a random username: user_<first8chars_of_uuid>
            username = f"user_{str(uuid.uuid4())[:8]}"

        # Create Supabase Auth user
        resp = await supabase.auth.sign_up(
            {
                "email": body.email,
                "password": body.password,
                "options": {
                    "data": {
                        "username": username,
                        "full_name": body.full_name,
                    }
                }
            }
        )

        if resp.user is None:
            raise HTTPException(status_code=500, detail="Supabase signup failed")

        # Create a profile row in "users" table
        profile_data = {
            "auth_user_id": resp.user.id,
            "username": username,
            "email": body.email,
            "full_name": body.full_name
        }

        await supabase_service.create_user(resp.user.id, profile_data)

        return {
            "success": True,
            "data": {
                "user": {
                    "id": resp.user.id,
                    "email": resp.user.email,
                    "username": username,
                    "full_name": body.full_name,
                },
                "message": "Signup successful. Please confirm your email."
            }
        }

    except Exception as e:
        logger.error(f"Signup error: {str(e)}")
        raise HTTPException(status_code=500, detail="An error occurred. Please try again.")


# -----------------------------
# LOGIN
# -----------------------------
@router.post("/login")
async def login(body: LoginRequest):
    try:
        resp = await supabase.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )

        if resp.session is None:
            raise HTTPException(status_code=401, detail="Invalid login")

        return {
            "success": True,
            "data": {
                "user": {
                    "id": resp.user.id,
                    "email": resp.user.email,
                    "username": resp.user.user_metadata.get("username"),
                    "full_name": resp.user.user_metadata.get("full_name"),
                },
                "session": {
                    "access_token": resp.session.access_token,
                    "refresh_token": resp.session.refresh_token,
                    "expires_in": resp.session.expires_in,
                }
            }
        }

    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=500, detail="An error occurred. Please try again.")


# -----------------------------
# ME (GET AUTH USER FROM JWT)
# -----------------------------
@router.get("/me")
async def me(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")

    token = authorization.split(" ")[1]

    try:
        resp = await supabase.auth.get_user(token)

        if resp.user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        return {
            "success": True,
            "data": {
                "id": resp.user.id,
                "email": resp.user.email,
                "username": resp.user.user_metadata.get("username"),
                "full_name": resp.user.user_metadata.get("full_name"),
                "email_confirmed": resp.user.email_confirmed_at,
                "created_at": resp.user.created_at,
            }
        }

    except Exception as e:
        logger.error(f"Token error: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed.")
