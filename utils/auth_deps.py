import os
import uuid
import logging
from typing import Optional, Dict, Any
from fastapi import Header, Query, HTTPException
from datetime import datetime
from services.supabase_service import supabase_service

logger = logging.getLogger(__name__)

async def get_current_user_from_token(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> Dict[str, Any]:
    actual_token = None
    if authorization and authorization.startswith("Bearer "):
        actual_token = authorization.split(" ", 1)[1].strip()
    elif token:
        actual_token = token
        
    if not actual_token:
        raise HTTPException(status_code=401, detail="Missing Authorization header or token")

    client = supabase_service.client

    try:
        # We need to handle the case where the client is not initialized yet
        if not client:
             from utils.supabase_client import get_client
             client = get_client()

        auth_resp = await client.auth.get_user(actual_token)
        auth_user_id = None

        if isinstance(auth_resp, dict):
            auth_user_id = auth_resp.get("data", {}).get("user", {}).get("id")
        else:
            auth_user_id = getattr(auth_resp.user, "id", None)

        if not auth_user_id:
            raise Exception("Invalid token")
            
        resp = await client.table("users").select("*").eq("auth_user_id", auth_user_id).limit(1).execute()
        user_row = resp.data[0] if resp.data else None

        # Auto-create user in public.users table if they don't exist
        if not user_row:
            logger.info(f"Auto-creating user in public.users table for auth_user_id: {auth_user_id}")
            
            # Get user metadata and email from auth response
            user_metadata = {}
            user_email = ""
            if isinstance(auth_resp, dict):
                user_data = auth_resp.get("data", {}).get("user", {})
                user_metadata = user_data.get("user_metadata", {})
                user_email = user_data.get("email", "")
            else:
                user_metadata = getattr(auth_resp.user, "user_metadata", {})
                user_email = getattr(auth_resp.user, "email", "")
            
            # Create user record
            new_user = {
                "auth_user_id": auth_user_id,
                "email": user_email,
                "username": user_metadata.get("username", f"user_{auth_user_id[:8]}"),
                "full_name": user_metadata.get("full_name", user_email.split("@")[0])
            }
            
            try:
                create_resp = await client.table("users").insert(new_user).execute()
                user_row = create_resp.data[0] if create_resp.data else None
                if not user_row:
                    raise Exception("Failed to create user in database")
            except Exception as create_err:
                logger.error(f"Failed to auto-create user: {create_err}")
                raise HTTPException(status_code=500, detail="Failed to create user account")

        return {
            "platform_user_id": user_row["id"],     # public.users.id
            "auth_user_id": auth_user_id,           # auth.users.id
            "email": user_row["email"],
            "full_name": user_row["full_name"],
            "username": user_row["username"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Authentication failed")
        raise HTTPException(status_code=401, detail="Authentication failed")

async def get_current_user_id(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> str:
    """Get the current authenticated user's platform_user_id"""
    user = await get_current_user_from_token(authorization, token)
    return user["platform_user_id"]

async def get_current_user_ids(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Get the current authenticated user with both IDs"""
    return await get_current_user_from_token(authorization, token)

async def get_optional_user_id(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> Optional[str]:
    """Get the current authenticated user's ID if present and valid, otherwise None"""
    try:
        if not authorization and not token:
            return None
        user = await get_current_user_from_token(authorization, token)
        return user.get("platform_user_id")
    except Exception:
        return None
