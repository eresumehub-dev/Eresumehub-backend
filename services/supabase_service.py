# services/supabase_service.py
"""
Supabase Service Layer
Handles all database operations for E-resumehub
"""

import os
import uuid
from typing import Optional, Dict, List, Any
from datetime import datetime
import logging
import asyncio
from supabase import AsyncClient
from dotenv import load_dotenv

# Use the centralized client to ensure single connection pool and consistent config
from utils.supabase_client import get_client

load_dotenv()

logger = logging.getLogger(__name__)

class SupabaseService:
    """Service class for Supabase operations"""
    
    def __init__(self):
        """Initialize SupabaseService"""
        logger.info("SupabaseService initialized")
        
    @property
    def client(self) -> AsyncClient:
        """Dynamically fetch the client to avoid httpx stale connection errors on Windows."""
        return get_client()
    
    # ============================================
    # USER OPERATIONS
    # ============================================
    
    async def create_user(self, auth_user_id: str, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new user in the database"""
        try:
            data = {
                "auth_user_id": auth_user_id,
                "username": user_data["username"],
                "email": user_data["email"],
                "full_name": user_data["full_name"],
                "headline": user_data.get("headline"),
                "location": user_data.get("location"),
            }
            
            response = await self.client.table("users").insert(data).execute()
            logger.info(f"User created: {user_data['username']}")
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Error creating user: {str(e)}")
            raise
    
    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username"""
        try:
            response = await self.client.table("users")\
                .select("*")\
                .eq("username", username)\
                .is_("deleted_at", "null")\
                .single()\
                .execute()
            
            return response.data if response.data else None
            
        except Exception as e:
            logger.error(f"Error getting user by username '{username}': {str(e)}")
            return None
    
    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        try:
            response = await self.client.table("users")\
                .select("*")\
                .eq("id", user_id)\
                .is_("deleted_at", "null")\
                .single()\
                .execute()
            
            return response.data if response.data else None
            
        except Exception as e:
            logger.error(f"Error getting user by platform ID '{user_id}': {str(e)}")
            return None
    
    async def update_user(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update user profile"""
        try:
            response = await self.client.table("users")\
                .update(updates)\
                .eq("id", user_id)\
                .execute()
            
            logger.info(f"User updated: {user_id}")
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Error updating user: {str(e)}")
            raise
    
    async def check_username_available(self, username: str) -> bool:
        """Check if username is available"""
        try:
            response = await self.client.rpc(
                "is_username_available",
                {"username_input": username}
            ).execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Error checking username: {str(e)}")
            return False
    
    async def get_user_profile_with_resumes(self, username: str) -> Optional[Dict[str, Any]]:
        """Get complete user profile with all resumes"""
        try:
            response = await self.client.rpc(
                "get_user_profile_with_resumes",
                {"username_input": username}
            ).execute()
            
            return response.data[0] if response.data else None
            
        except Exception as e:
            logger.error(f"Error getting user profile: {str(e)}")
            return None
    
    # ============================================
    # RESUME OPERATIONS
    # ============================================
    
    async def create_resume(self, user_id: str, resume_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new resume"""
        try:
            # Generate unique slug
            base_slug = resume_data.get("slug")
            slug = await self.generate_unique_slug(user_id, base_slug)
            
            data = {
                "user_id": user_id,
                "slug": slug,
                "title": resume_data["title"],
                "resume_data": resume_data["resume_data"],
                "country": resume_data["country"],
                "language": resume_data.get("language", "English"),
                "template_style": resume_data.get("template_style", "professional"),
                "visibility": resume_data.get("visibility", "public"),
                "is_default": resume_data.get("is_default", False),
                "tags": resume_data.get("tags", []),
            }
            
            response = await self.client.table("resumes").insert(data).execute()
            logger.info(f"Resume created: {slug} for user {user_id}")
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Error creating resume: {str(e)}")
            raise
    
    async def get_resume(self, resume_id: str) -> Optional[Dict[str, Any]]:
        """Get resume by ID"""
        try:
            response = await self.client.table("resumes")\
                .select("*")\
                .eq("id", resume_id)\
                .is_("deleted_at", "null")\
                .single()\
                .execute()
            
            return response.data if response.data else None
            
        except Exception as e:
            logger.error(f"Error getting resume: {str(e)}")
            return None
    
    async def get_resume_by_slug(self, username: str, slug: str) -> Optional[Dict[str, Any]]:
        """Get resume by username and slug (public view)"""
        try:
            clean_username = username.strip().lower()
            clean_slug = slug.strip().lower()
            logger.info(f"Public lookup for username: '{clean_username}', slug: '{clean_slug}'")
            
            # 1. Find the user ID for this username (case-insensitive)
            # Use 'maybe_single' or check length to avoid exception if not found
            user_response = await self.client.table("users")\
                .select("id, username")\
                .ilike("username", clean_username)\
                .execute()
            
            if not user_response.data:
                logger.warning(f"Public lookup failed: User '{clean_username}' not found")
                return None
                
            user_id = user_response.data[0]["id"]
            actual_username = user_response.data[0]["username"]
            logger.info(f"User found: {actual_username} (ID: {user_id})")
            
            # 2. Get the resume for this user ID and slug (case-insensitive)
            resume_response = await self.client.table("resumes")\
                .select("*")\
                .eq("user_id", user_id)\
                .ilike("slug", clean_slug)\
                .is_("deleted_at", "null")\
                .execute()
            
            if not resume_response.data:
                logger.warning(f"Public lookup failed: No resume found with slug '{clean_slug}' for user_id '{user_id}'")
                return None
                
            resume = resume_response.data[0]
            visibility = resume.get("visibility", "private")
            logger.info(f"Resume found: {resume['id']} | Title: {resume['title']} | Visibility: {visibility}")
            
            # Check visibility
            if visibility in ["public", "unlisted"]:
                return resume
            else:
                logger.warning(f"Public access denied: Resume '{slug}' visibility is '{visibility}'")
                return None
            
        except Exception as e:
            logger.error(f"Error in get_resume_by_slug: {str(e)}")
            return None
    
    async def update_resume(self, resume_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update resume"""
        try:
            response = await self.client.table("resumes")\
                .update(updates)\
                .eq("id", resume_id)\
                .execute()
            
            logger.info(f"Resume updated: {resume_id}")
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Error updating resume: {str(e)}")
            raise
    
    async def delete_resume(self, resume_id: str) -> bool:
        """Soft delete resume"""
        try:
            response = await self.client.table("resumes")\
                .update({"deleted_at": datetime.utcnow().isoformat()})\
                .eq("id", resume_id)\
                .execute()
            
            logger.info(f"Resume deleted: {resume_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting resume: {str(e)}")
            return False
    
    async def get_user_resumes(self, user_id: Any) -> List[Dict[str, Any]]:
        """Get all resumes for a user. Supports single ID or list of IDs."""
        try:
            query = self.client.table("resumes").select("*")
            
            if isinstance(user_id, list):
                # Use OR filter for multiple IDs
                id_filter = ",".join([f"user_id.eq.{uid}" for uid in user_id if uid])
                query = query.or_(id_filter)
            else:
                query = query.eq("user_id", user_id)
            
            response = await query.is_("deleted_at", "null")\
                .order("is_default", desc=True)\
                .order("created_at", desc=True)\
                .execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Error getting user resumes: {str(e)}")
            return []
    
    async def generate_unique_slug(self, user_id: str, base_slug: str) -> str:
        """Generate unique slug for resume"""
        try:
            response = await self.client.rpc(
                "generate_unique_resume_slug",
                {
                    "user_id_input": user_id,
                    "base_slug": base_slug
                }
            ).execute()
            
            return response.data if response.data else base_slug
            
        except Exception as e:
            logger.error(f"Error generating slug: {str(e)}")
            return base_slug
    
    # ============================================
    # ANALYTICS OPERATIONS
    # ============================================
    
    async def log_resume_view(self, resume_id: str, view_data: Dict[str, Any]) -> str:
        """Log a resume view for analytics"""
        try:
            # Generate UUID locally to ensure we have it regardless of RLS return policies
            view_id = str(uuid.uuid4())

            # Ensure empty strings for UUIDs are treated as None
            for key in ["session_id", "viewer_user_id"]:
                if view_data.get(key) == "":
                    view_data[key] = None

            data = {
                "id": view_id,
                "resume_id": resume_id,
                "session_id": view_data.get("session_id"),
                "viewer_user_id": view_data.get("viewer_user_id"),
                "visitor_ip": view_data.get("visitor_ip"),
                "visitor_country": view_data.get("visitor_country"),
                "visitor_city": view_data.get("visitor_city"),
                "device_type": view_data.get("device_type"),
                "browser": view_data.get("browser"),
                "referrer": view_data.get("referrer"),
                "screen_size": view_data.get("screen_size"), # New behavioral signal (v11.0.0)
                "max_scroll_depth": view_data.get("max_scroll_depth", 0), # New behavioral signal (v11.0.0)
                "utm_source": view_data.get("utm_source"),
                "utm_medium": view_data.get("utm_medium"),
                "utm_campaign": view_data.get("utm_campaign"),
            }
            
            logger.info(f"Attempting to log view for resume {resume_id} with data: {data}")
            # We don't need .select() anymore since we generated the ID
            await self.client.table("resume_views").insert(data).execute()
            logger.info(f"Resume view logged: {resume_id} (ID: {view_id})")
            
            return view_id
            
        except Exception as e:
            logger.error(f"Error logging view for resume {resume_id}: {str(e)}", exc_info=True)
            raise e # Re-raise to let the router handle it and return the error to the client

    async def update_view_duration(self, view_id: str, pulse_data: Any) -> bool:
        """Update the duration and behavioral signals for a view session (v11.0.0)"""
        try:
            # Pulse data can be simple int (legacy) or dict (v11.0.0)
            update_payload = {}
            if isinstance(pulse_data, dict):
                 update_payload = {
                     "duration_seconds": pulse_data.get("duration_seconds"),
                     "max_scroll_depth": pulse_data.get("max_scroll_depth"),
                     "is_active": pulse_data.get("is_active", True),
                     "exit_point": pulse_data.get("exit_point")
                 }
            else:
                 update_payload = {"duration_seconds": pulse_data}

            # Filter out None values to avoid overwriting existing data with nulls
            update_payload = {k: v for k, v in update_payload.items() if v is not None}

            await self.client.table("resume_views")\
                .update(update_payload)\
                .eq("id", view_id)\
                .execute()
            return True
        except Exception as e:
            logger.error(f"Error updating behavioral heartbeat: {str(e)}")
            return False
    
    async def log_profile_view(self, profile_user_id: str, view_data: Dict[str, Any]) -> bool:
        """Log a profile view"""
        try:
            data = {
                "profile_user_id": profile_user_id,
                "viewer_user_id": view_data.get("viewer_user_id"),
                "visitor_ip": view_data.get("visitor_ip"),
                "visitor_country": view_data.get("visitor_country"),
                "device_type": view_data.get("device_type"),
                "browser": view_data.get("browser"),
                "referrer": view_data.get("referrer"),
            }
            
            # Using insert with no select since we don't need the ID back here
            await self.client.table("user_profile_views").insert(data).execute()
            return True
            
        except Exception as e:
            logger.error(f"Error logging profile view: {str(e)}")
            return False
    
    async def log_resume_download(self, resume_id: str, download_data: Dict[str, Any]) -> bool:
        """Log a resume download"""
        try:
            # Generate ID locally
            download_id = str(uuid.uuid4())

            data = {
                "id": download_id,
                "resume_id": resume_id,
                "session_id": download_data.get("session_id"),
                "downloader_user_id": download_data.get("downloader_user_id"),
                "visitor_ip": download_data.get("visitor_ip"),
                "visitor_country": download_data.get("visitor_country"),
                "device_type": download_data.get("device_type"),
            }
            
            await self.client.table("resume_downloads").insert(data).execute()
            logger.info(f"Resume download logged: {resume_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error logging download: {str(e)}")
            return False
    
    async def get_resume_analytics(self, resume_id: str) -> Dict[str, Any]:
        """Get analytics for a resume"""
        try:
            # Get view stats
            views = await self.client.table("resume_views")\
                .select("*", count="exact")\
                .eq("resume_id", resume_id)\
                .execute()
            
            # Get download stats
            downloads = await self.client.table("resume_downloads")\
                .select("*", count="exact")\
                .eq("resume_id", resume_id)\
                .execute()
            
            # Get geographic distribution
            geo_data = await self.client.table("resume_views")\
                .select("visitor_country")\
                .eq("resume_id", resume_id)\
                .execute()
            
            countries = {}
            for row in geo_data.data:
                country = row.get("visitor_country", "Unknown")
                countries[country] = countries.get(country, 0) + 1
            
            return {
                "total_views": views.count,
                "total_downloads": downloads.count,
                "geographic_distribution": countries,
                "views_data": views.data[:100],  # Last 100 views
            }
            
        except Exception as e:
            logger.error(f"Error getting analytics: {str(e)}")
            return {}
    
    # ============================================
    # STORAGE OPERATIONS
    # ============================================
    
    async def upload_resume_pdf(self, user_id: str, resume_id: str, file_data: bytes, filename: str) -> str:
        """Upload resume PDF to storage with robust conflict handling"""
        try:
            path = f"{user_id}/{resume_id}/{filename}"
            
            # Try upload with upsert
            try:
                await self.client.storage.from_("resumes-pdf").upload(
                    path,
                    file_data,
                    {"content-type": "application/pdf", "upsert": True, "cache-control": "no-cache"}
                )
            except Exception as e:
                # If conflict (409) or other error, try update/overwrite
                logger.warning(f"Initial PDF upload failed, attempting overwrite: {str(e)}")
                try:
                    await self.client.storage.from_("resumes-pdf").update(
                        path,
                        file_data,
                        {"content-type": "application/pdf", "cache-control": "no-cache", "upsert": True}
                    )
                except Exception as update_err:
                     logger.error(f"Overwrite failed too: {update_err}")
                     # Try delete and re-upload as a clearer "nuclear" option is safer than cryptic 409s
                     try:
                        await self.client.storage.from_("resumes-pdf").remove([path])
                        await self.client.storage.from_("resumes-pdf").upload(
                            path,
                            file_data,
                            {"content-type": "application/pdf", "cache-control": "no-cache"}
                        )
                     except Exception as e2:
                        logger.error(f"Final re-upload attempt failed: {str(e2)}")
                        raise e2
            
            # Get public URL
            url = await self.client.storage.from_("resumes-pdf").get_public_url(path)
            
            logger.info(f"PDF uploaded: {path}")
            return url
            
        except Exception as e:
            logger.error(f"Error uploading PDF: {str(e)}")
            raise

    async def get_resume_signed_url(self, user_id: str, resume_id: str, expires_in: int = 60) -> str:
        """Generate a temporary signed URL for zero-memory direct download (Staff+ Optimized)."""
        try:
            # Construct path: resumes/{user_id}/{resume_id}.pdf
            # Note: The prompt indicated the storage path is f"{user_id}/{resume_id}/{filename}" 
            # in upload_resume_pdf. Let's find the current filename if possible or assume default.
            
            # 1. Fetch resume to get the filename/slug used during upload
            resume = await self.get_resume(resume_id)
            if not resume:
                raise ValueError("Resume not found")
                
            filename = f"{resume.get('slug')}.pdf"
            path = f"{user_id}/{resume_id}/{filename}"
            
            # 2. Create signed URL (60s is plenty for a browser to start the stream)
            response = await self.client.storage.from_("resumes-pdf").create_signed_url(path, expires_in)
            
            # The new SDK returns a dict with 'signedURL' (or equivalent)
            if isinstance(response, dict) and "signedURL" in response:
                return response["signedURL"]
                
            # Fallback for different SDK versions
            return getattr(response, "signed_url", str(response))
            
        except Exception as e:
            logger.error(f"Error creating signed URL for {resume_id}: {e}")
            raise
    
    async def upload_thumbnail(self, user_id: str, resume_id: str, image_data: bytes, filename: str) -> str:
        """Upload resume thumbnail"""
        try:
            path = f"{user_id}/{resume_id}/{filename}"
            
            response = await self.client.storage.from_("resumes-thumbnails").upload(
                path,
                image_data,
                {"content-type": "image/png"}
            )
            
            url = await self.client.storage.from_("resumes-thumbnails").get_public_url(path)
            
            logger.info(f"Thumbnail uploaded: {path}")
            return url
            
        except Exception as e:
            logger.error(f"Error uploading thumbnail: {str(e)}")
            raise

    async def upload_profile_picture(self, user_id: str, file_data: bytes, filename: str) -> str:
        """Upload profile picture to storage"""
        try:
            bucket_name = "profile-pictures"
            
            # Ensure bucket exists (blind create is safer than get+update if permissions are tight)
            try:
                await self.client.storage.create_bucket(bucket_name, {"public": True})
            except Exception:
                # Ignore error (likely already exists)
                pass

            path = f"{user_id}/{filename}"
            
            # Determine content type from filename
            ext = filename.split('.')[-1].lower() if '.' in filename else 'jpg'
            mime_type = "image/png" if ext == 'png' else "image/jpeg"
            if ext in ['webp']: mime_type = "image/webp"
            
            # Use upsert to overwrite existing photo
            await self.client.storage.from_(bucket_name).upload(
                path,
                file_data,
                {"content-type": mime_type, "upsert": "true"}
            )
            
            # Force get public URL
            url = await self.client.storage.from_(bucket_name).get_public_url(path)
            
            logger.info(f"Profile picture uploaded for user {user_id}: {url}")
            return url
            
        except Exception as e:
            logger.error(f"Error uploading profile picture: {str(e)}")
            raise
    
    async def delete_file(self, bucket: str, path: str) -> bool:
        """Delete file from storage"""
        try:
            response = await self.client.storage.from_(bucket).remove([path])
            logger.info(f"File deleted: {path}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting file: {str(e)}")
            return False
    
    # ============================================
    # SOCIAL FEATURES
    # ============================================
    
    async def follow_user(self, follower_id: str, following_id: str) -> bool:
        """Follow a user"""
        try:
            data = {
                "follower_id": follower_id,
                "following_id": following_id
            }
            
            response = await self.client.table("user_follows").insert(data).execute()
            logger.info(f"User {follower_id} followed {following_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error following user: {str(e)}")
            return False
    
    async def unfollow_user(self, follower_id: str, following_id: str) -> bool:
        """Unfollow a user"""
        try:
            response = await self.client.table("user_follows")\
                .delete()\
                .eq("follower_id", follower_id)\
                .eq("following_id", following_id)\
                .execute()
            
            logger.info(f"User {follower_id} unfollowed {following_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error unfollowing user: {str(e)}")
            return False
    
    async def like_resume(self, user_id: str, resume_id: str) -> bool:
        """Like a resume"""
        try:
            data = {
                "user_id": user_id,
                "resume_id": resume_id
            }
            
            response = await self.client.table("resume_likes").insert(data).execute()
            logger.info(f"User {user_id} liked resume {resume_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error liking resume: {str(e)}")
            return False
    
    async def unlike_resume(self, user_id: str, resume_id: str) -> bool:
        """Unlike a resume"""
        try:
            response = await self.client.table("resume_likes")\
                .delete()\
                .eq("user_id", user_id)\
                .eq("resume_id", resume_id)\
                .execute()
            
            logger.info(f"User {user_id} unliked resume {resume_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error unliking resume: {str(e)}")
            return False
    
    # ============================================
    # NOTIFICATIONS
    # ============================================
    
    async def get_user_notifications(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get user's notifications"""
        try:
            response = await self.client.table("notifications")\
                .select("*")\
                .eq("user_id", user_id)\
                .order("created_at", desc=True)\
                .limit(limit)\
                .execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Error getting notifications: {str(e)}")
            return []
    
    async def mark_notification_read(self, notification_id: str) -> bool:
        """Mark notification as read"""
        try:
            response = await self.client.table("notifications")\
                .update({"is_read": True, "read_at": datetime.utcnow().isoformat()})\
                .eq("id", notification_id)\
                .execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Error marking notification read: {str(e)}")
            return False

    async def create_audit_log(self, user_id: str, action: str, entity_type: str = None, entity_id: str = None, old_data: Dict = None, new_data: Dict = None) -> bool:
        """Persist an entry to the audit_log table"""
        try:
            data = {
                "user_id": user_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "old_data": old_data,
                "new_data": new_data,
                "created_at": datetime.utcnow().isoformat()
            }
            await self.client.table("audit_log").insert(data).execute()
            return True
        except Exception as e:
            logger.error(f"Error creating audit log: {str(e)}")
            return False

    # ============================================
    # NUDGE ENGINE (v14.0.0)
    # ============================================

    async def get_user_nudge_states(self, user_id: str) -> List[Dict[str, Any]]:
        """Fetch all nudge states for a user to avoid repeats."""
        try:
            response = await self.client.table("user_nudge_state")\
                .select("*")\
                .eq("user_id", user_id)\
                .execute()
            return response.data
        except Exception as e:
            logger.error(f"Error fetching nudge states: {str(e)}")
            return []

    async def update_user_nudge_state(self, user_id: str, nudge_type: str, resume_id: str, status: str, confidence: float = 0.0) -> bool:
        """Upsert a nudge state (seen/dismissed/acted)."""
        try:
            data = {
                "user_id": user_id,
                "nudge_type": nudge_type,
                "resume_id": resume_id,
                "status": status,
                "confidence_at_trigger": confidence,
                "updated_at": datetime.utcnow().isoformat()
            }
            # Use upsert based on user/resume/type composite (if unique constraint existed)
            # For now, we update if exists, else insert
            existing = await self.client.table("user_nudge_state")\
                .select("id")\
                .eq("user_id", user_id)\
                .eq("nudge_type", nudge_type)\
                .eq("resume_id", resume_id)\
                .execute()

            if existing.data:
                await self.client.table("user_nudge_state")\
                    .update(data)\
                    .eq("id", existing.data[0]["id"])\
                    .execute()
            else:
                await self.client.table("user_nudge_state").insert(data).execute()
            
            return True
        except Exception as e:
            logger.error(f"Error updating nudge state: {str(e)}")
            return False

# Global instance
supabase_service = SupabaseService()