import os
import uuid
import logging
from typing import Optional, Dict, Any
from fastapi import Header, Query, HTTPException, Request
from datetime import datetime
from services.supabase_service import supabase_service

logger = logging.getLogger(__name__)

async def get_current_user_from_token(
    request: Request,
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

            # 1. INTELLIGENT ACCOUNT LINKING: Check if user exists by email
            if user_email:
                email_resp = await client.table("users").select("*").eq("email", user_email).limit(1).execute()
                if email_resp.data:
                    existing_user = email_resp.data[0]
                    logger.info(f"Found existing user record for email: {user_email}. Linking auth_user_id: {auth_user_id}")
                    # Update existing record with the new auth_user_id
                    update_resp = await client.table("users").update({"auth_user_id": auth_user_id}).eq("id", existing_user["id"]).execute()
                    if update_resp.data:
                        user_row = update_resp.data[0]
            
            # 1b. USERNAME FALLBACK (Identity Fusion): Check by username if no email match
            if not user_row:
                base_username = user_metadata.get("username") or user_email.split("@")[0]
                if base_username:
                    user_resp = await client.table("users").select("*").eq("username", base_username).limit(1).execute()
                    if user_resp.data:
                        existing_user = user_resp.data[0]
                        logger.info(f"IDENTITY FUSION: Found existing account by username '{base_username}'. Merging {user_email} (ID: {auth_user_id}) into it.")
                        # Link this account to the new Auth ID
                        update_resp = await client.table("users").update({"auth_user_id": auth_user_id, "email": user_email}).eq("id", existing_user["id"]).execute()
                        if update_resp.data:
                            user_row = update_resp.data[0]
            
            # 2. CREATE NEW USER (if not linked)
            if not user_row:
                logger.info(f"Auto-creating user in public.users table for auth_user_id: {auth_user_id}")
                
                base_username = user_metadata.get("username") or user_email.split("@")[0]
                full_name = user_metadata.get("full_name") or user_email.split("@")[0]
                
                # COLLISION RESISTANCE: Try to generate a unique username if taken
                max_retries = 3
                current_username = base_username
                
                for attempt in range(max_retries):
                    new_user = {
                        "auth_user_id": auth_user_id,
                        "email": user_email,
                        "username": current_username,
                        "full_name": full_name
                    }
                    
                    try:
                        create_resp = await client.table("users").insert(new_user).execute()
                        user_row = create_resp.data[0] if create_resp.data else None
                        if user_row:
                            break
                    except Exception as create_err:
                        err_str = str(create_err)
                        if "23505" in err_str or "duplicate key" in err_str:
                            # Username collision!
                            suffix = str(uuid.uuid4())[:4]
                            current_username = f"{base_username}_{suffix}"
                            logger.warning(f"Username conflict for '{base_username}', retrying as '{current_username}'")
                        else:
                            logger.error(f"Failed to auto-create user (Attempt {attempt+1}): {create_err}")
                            if attempt == max_retries - 1:
                                raise HTTPException(status_code=500, detail="Failed to create user account")

            if not user_row:
                raise Exception("Failed to resolve or create user in database")

        # 3. Injection into Request State for Middleware (v16.3.2 Alignment)
        request.state.user_id = auth_user_id
        
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
    request: Request,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> str:
    """Get the current authenticated user's canonical auth_user_id (v16.0.0)"""
    user = await get_current_user_from_token(request, authorization, token)
    return user["auth_user_id"]

async def get_current_user_ids(
    request: Request,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Get the current authenticated user with both IDs"""
    return await get_current_user_from_token(request, authorization, token)

async def get_optional_user_id(
    request: Request,
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None)
) -> Optional[str]:
    """Get the current authenticated user's canonical ID (v16.0.0) if present and valid, otherwise None"""
    try:
        if not authorization and not token:
            return None
        user = await get_current_user_from_token(request, authorization, token)
        return user.get("auth_user_id")
    except Exception:
        return None
