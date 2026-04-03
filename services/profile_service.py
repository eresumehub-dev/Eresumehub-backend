"""
User Profile Service
Handles CRUD operations for user profiles, work experiences, and education
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, date, timezone
from services.supabase_service import supabase_service
import asyncio
from services.cache_service import cache_service
import logging

logger = logging.getLogger(__name__)


class ProfileService:
    # Redis Cache Keys (v15.0.0)
    CACHE_PREFIX = "boot_fast:"
    CACHE_TTL = 900 # 15 minutes

    def __init__(self, supabase_service):
        self.supabase = supabase_service

    @classmethod
    def invalidate_cache(cls, user_id: str):
        """Standard Cache Busting: Clear Redis (v15.0.0)"""
        cache_service.delete(f"{cls.CACHE_PREFIX}{user_id}")
        logger.info(f"BOOTSTRAP-FAST Cache Busted for user {user_id}")
    
    # ==================== Profile CRUD ====================
    
    async def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        [EXPLICIT: FULL GRAPH] (v15.1.0)
        Get entire profile graph in ONE database call.
        Reserved for Resume Editor and Detailed views.
        """
        try:
            # 1. Single Graph Query: Profile + All sub-collections
            response = await self.supabase.client.table('user_profiles')\
                .select("""
                    *,
                    work_experiences(*),
                    educations(*),
                    projects(*),
                    certifications(*),
                    profile_extras(*)
                """)\
                .eq('user_id', user_id)\
                .execute()
            
            # 2. Fallback check
            if not response.data:
                user_res = await self.supabase.client.table('users').select('auth_user_id').eq('id', user_id).execute()
                if user_res.data:
                    auth_id = user_res.data[0]['auth_user_id']
                    response = await self.supabase.client.table('user_profiles')\
                        .select('*, work_experiences(*), educations(*), projects(*), certifications(*), profile_extras(*)')\
                        .eq('user_id', auth_id).execute()

            if not response.data:
                return None
            
            profile = response.data[0]
            
            # 3. Defensive Post-processing
            for key in ['work_experiences', 'educations', 'projects', 'certifications']:
                if profile.get(key):
                    profile[key].sort(key=lambda x: x.get('display_order', 0))
                else:
                    profile[key] = []
            
            extras_list = profile.get('profile_extras', [])
            profile['extras'] = extras_list[0] if extras_list else {}
            
            return profile
            
        except Exception as e:
            logger.error(f"Error fetching full profile for user {user_id}: {str(e)}")
            raise

    async def get_profile_header(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        [OPTIMIZED: FAST SHELL] (v15.1.0)
        Fetches ONLY the fields required for the Dashboard Header/Nav.
        Payload size target: <2KB.
        """
        try:
            response = await self.supabase.client.table('user_profiles')\
                .select("id, user_id, full_name, headline, bio, photo_url, location")\
                .eq('user_id', user_id)\
                .execute()
            
            if not response.data:
                return None
            
            profile = response.data[0]
            # Add shallow defaults to maintain contract
            profile.update({
                "work_experiences": [], "educations": [], "projects": [], 
                "certifications": [], "extras": {}
            })
            return profile
        except Exception as e:
            logger.error(f"Error fetching profile header for user {user_id}: {str(e)}")
            return None

    async def get_dashboard_bootstrap(self, user_id: str) -> Dict[str, Any]:
        """
        The 'Fast Bootstrap' Orchestrator with Stale-While-Revalidate (v15.2.0)
        - SOFT_TTL = 15m (Stale-While-Revalidate window starts)
        - HARD_TTL = 60m (Hard expiration)
        - GUARANTEE: Returns data <150ms if any cache exists.
        """
        SOFT_TTL = 900  # 15 minutes
        HARD_TTL = 3600 # 60 minutes
        cache_key = f"{self.CACHE_PREFIX}{user_id}"
        now = datetime.now(timezone.utc).timestamp()

        try:
            # 1. Fetch from Redis
            cached_container = cache_service.get(cache_key)
            if cached_container:
                data = cached_container.get('data')
                soft_expires_at = cached_container.get('soft_expires_at', 0)

                # Tier A: Fresh Cache (<15m)
                if now < soft_expires_at:
                    return data
                
                # Tier B: Stale-While-Revalidate (>15m but <60m)
                # Attempt to lock for recomputation so only ONE task clears it
                lock_key = f"recompute_lock:{user_id}"
                if cache_service.set_nx(lock_key, "locked", ttl_seconds=60):
                    logger.info(f"SWR: Triggering background recompute for stale bootstrap cache (User: {user_id})")
                    # Fire-and-forget the refresh
                    asyncio.create_task(self._recompute_and_cache_bootstrap(user_id, SOFT_TTL, HARD_TTL))
                
                return data

            # 3. Tier C: Cache MISS (First time or expired >60m)
            # Perform blocking fetch
            return await self._recompute_and_cache_bootstrap(user_id, SOFT_TTL, HARD_TTL)

        except Exception as e:
            logger.error(f"Error in Dashboard Bootstrap (v15.2.0): {e}")
            # Final Fallback: Return empty structure
            return {"exists": False, "profile": {}, "resumes": []}

    async def _recompute_and_cache_bootstrap(self, user_id: str, soft_ttl: int, hard_ttl: int) -> Dict[str, Any]:
        """Computes the full bootstrap payload and saves to Redis with SWR metadata (v15.2.0)"""
        try:
            from services.resume_service import ResumeService
            resume_service = ResumeService(self.supabase)
            
            # 1. Parallel Fetch (Header & Resumes ONLY)
            results = await asyncio.gather(
                self.get_profile_header(user_id),
                resume_service.get_user_resumes_v2(user_id)
            )
            
            profile = results[0]
            resumes = results[1]
            
            payload = {
                "exists": profile is not None,
                "profile": profile or {},
                "resumes": resumes or [],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            # 2. Update Cache with Container-Wrapper
            container = {
                "data": payload,
                "soft_expires_at": datetime.now(timezone.utc).timestamp() + soft_ttl
            }
            cache_service.set(f"{self.CACHE_PREFIX}{user_id}", container, ttl_seconds=hard_ttl)
            
            return payload
        except Exception as e:
            logger.error(f"Recompute Failure for user {user_id}: {e}")
            return {"exists": False, "profile": {}, "resumes": []}
    
    async def create_or_update_profile(self, user_id: str, profile_data: Dict[str, Any], is_ai_import: bool = False) -> Dict[str, Any]:
        """Create or update user profile"""
        try:
            # Standard Identity Logic (v3.16.0)
            # Ensure we update the correct profile, preferring Auth ID for the FK
            user_res = await self.supabase.client.table('users').select('auth_user_id').eq('id', user_id).execute()
            auth_user_id = user_res.data[0]['auth_user_id'] if user_res.data else user_id
            
            # Check if profile exists using Auth ID
            existing = await self.supabase.client.table('user_profiles')\
                .select('*')\
                .eq('user_id', auth_user_id)\
                .execute()
            
            existing_profile = existing.data[0] if existing.data else None
            
            profile_payload = {
                'user_id': auth_user_id,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            # Only update fields that are actually provided in profile_data
            fields_to_check = [
                'full_name', 'email', 'phone', 'city', 'country', 
                'street_address', 'postal_code', 'nationality', 'date_of_birth',
                'linkedin_url', 'photo_url', 'professional_summary', 
                'skills', 'languages', 'links', 'headline', 'motivation', 'self_pr'
            ]
            
            for field in fields_to_check:
                if field in profile_data:
                    val = profile_data[field]
                    
                    # AI IMPORT SAFETY: Don't overwrite existing db fields with empty AI strings
                    if is_ai_import and not val and existing_profile and existing_profile.get(field):
                        continue
                    
                    # Special handling for photo_url: Don't overwrite existing with None unless explicit
                    if field == 'photo_url' and not val:
                         continue
                    
                    if field == 'date_of_birth' and val == "":
                        profile_payload[field] = None
                    elif field in ['skills', 'languages', 'links']:
                        # Defensive Normalization (v6.4.0): Force list types
                        if val is None: profile_payload[field] = []
                        elif isinstance(val, list): profile_payload[field] = val
                        elif isinstance(val, str) and val.strip(): profile_payload[field] = [val.strip()]
                        else: profile_payload[field] = []
                    else:
                        profile_payload[field] = val
            
            if existing_profile:
                # Update existing profile
                profile_id = existing_profile['id']
                response = await self.supabase.client.table('user_profiles')\
                    .update(profile_payload)\
                    .eq('id', profile_id)\
                    .execute()
            else:
                # Create new profile
                response = await self.supabase.client.table('user_profiles')\
                    .insert(profile_payload)\
                    .execute()
                profile_id = response.data[0]['id']
            
            # Handle work experiences (Skip overwrite if AI import has none but DB has some)
            if 'work_experiences' in profile_data:
                should_update_work = not (is_ai_import and not profile_data['work_experiences'])
                if should_update_work:
                    await self._update_work_experiences(profile_id, profile_data['work_experiences'])
            
            # Handle education
            if 'educations' in profile_data:
                should_update_edu = not (is_ai_import and not profile_data['educations'])
                if should_update_edu:
                    await self._update_educations(profile_id, profile_data['educations'])

            # Handle projects
            if 'projects' in profile_data:
                should_update_proj = not (is_ai_import and not profile_data['projects'])
                if should_update_proj:
                    await self._update_projects(profile_id, profile_data['projects'])
            
            # Handle certifications
            if 'certifications' in profile_data:
                should_update_cert = not (is_ai_import and not profile_data['certifications'])
                if should_update_cert:
                    await self.supabase.client.table('user_profiles').upsert(profile_payload).execute()
            
            # Cache Invalidation (v10.0.0)
            self.invalidate_cache(user_id)
            from services.analytics_service import AnalyticsService
            AnalyticsService.invalidate_user_cache(user_id)

            logger.info(f"Profile for user {user_id} created/updated")
            return profile_payload
        except Exception as e:
            logger.error(f"Error creating/updating profile: {str(e)}")
            raise
    
    # ==================== Work Experience CRUD ====================
    
    async def _update_work_experiences(self, profile_id: str, experiences: List[Dict[str, Any]]):
        """Update work experiences for a profile using bulk insert"""
        try:
            # Delete existing experiences
            await self.supabase.client.table('work_experiences')\
                .delete()\
                .eq('profile_id', profile_id)\
                .execute()
            
            if not experiences:
                return

            # Prepare bulk insert payloads
            payloads = []
            for idx, exp in enumerate(experiences):
                # Type Guard: Ensure achievements is a list (v6.1.0)
                achievements = exp.get('achievements', [])
                if isinstance(achievements, str):
                    achievements = [achievements]
                elif not isinstance(achievements, list):
                    achievements = []

                payloads.append({
                    'profile_id': profile_id,
                    'job_title': exp.get('job_title') or "Professional",
                    'company': exp.get('company') or "Independent",
                    'city': exp.get('city'),
                    'country': exp.get('country'),
                    'location': exp.get('location'),
                    'start_date': self._sanitize_date(exp.get('start_date')),
                    'end_date': self._sanitize_date(exp.get('end_date')),
                    'is_current': exp.get('is_current', False),
                    'achievements': exp.get('achievements', []),
                    'display_order': idx
                })
            
            # Bulk insert
            await self.supabase.client.table('work_experiences').insert(payloads).execute()
            logger.info(f"Bulk inserted {len(payloads)} work experiences for profile {profile_id}")
                
        except Exception as e:
            logger.error(f"Error updating work experiences: {str(e)}")
            raise

    async def add_work_experience(self, profile_id: str, experience: Dict[str, Any]) -> Dict[str, Any]:
        """Add a single work experience"""
        try:
            # Get current count for display_order
            count_response = await self.supabase.client.table('work_experiences')\
                .select('id', count='exact')\
                .eq('profile_id', profile_id)\
                .execute()
            
            exp_payload = {
                'profile_id': profile_id,
                'job_title': experience.get('job_title'),
                'company': experience.get('company'),
                'location': experience.get('location'),
                'start_date': self._sanitize_date(experience.get('start_date')),
                'end_date': self._sanitize_date(experience.get('end_date')),
                'is_current': experience.get('is_current', False),
                'achievements': experience.get('achievements', []),
                'display_order': count_response.count or 0
            }
            
            response = await self.supabase.client.table('work_experiences').insert(exp_payload).execute()
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Error adding work experience: {str(e)}")
            raise
    
    async def delete_work_experience(self, experience_id: str):
        """Delete a work experience"""
        try:
            await self.supabase.client.table('work_experiences').delete().eq('id', experience_id).execute()
        except Exception as e:
            logger.error(f"Error deleting work experience: {str(e)}")
            raise
    
    # ==================== Education CRUD ====================
    
    async def _update_educations(self, profile_id: str, educations: List[Dict[str, Any]]):
        """Update educations for a profile using bulk insert"""
        try:
            # Delete existing educations
            await self.supabase.client.table('educations')\
                .delete()\
                .eq('profile_id', profile_id)\
                .execute()
            
            if not educations:
                return

            # Prepare bulk insert payloads
            payloads = []
            for idx, edu in enumerate(educations):
                payloads.append({
                    'profile_id': profile_id,
                    'degree': edu.get('degree'),
                    'field_of_study': edu.get('field_of_study'),
                    'institution': edu.get('institution'),
                    'city': edu.get('city'),
                    'country': edu.get('country'),
                    'location': edu.get('location'),
                    'graduation_date': self._sanitize_date(edu.get('graduation_date')),
                    'gpa': edu.get('gpa'),
                    'display_order': idx
                })
            
            # Bulk insert
            await self.supabase.client.table('educations').insert(payloads).execute()
            logger.info(f"Bulk inserted {len(payloads)} educations for profile {profile_id}")
                
        except Exception as e:
            logger.error(f"Error updating educations: {str(e)}")
            raise

    async def _update_projects(self, profile_id: str, projects: List[Dict[str, Any]]):
        """Update projects for a profile using bulk insert"""
        try:
            # Delete existing projects
            await self.supabase.client.table('projects')\
                .delete()\
                .eq('profile_id', profile_id)\
                .execute()
            
            if not projects:
                return

            # Prepare bulk insert payloads
            payloads = []
            for idx, proj in enumerate(projects):
                payloads.append({
                    'profile_id': profile_id,
                    'title': proj.get('title'),
                    'role': proj.get('role'),
                    'link': proj.get('link'),
                    'description': proj.get('description'),
                    'technologies': proj.get('technologies', []),
                    'start_date': self._sanitize_date(proj.get('start_date')),
                    'end_date': self._sanitize_date(proj.get('end_date')),
                    'is_current': proj.get('is_current', False),
                    'display_order': idx
                })
            
            # Bulk insert
            await self.supabase.client.table('projects').insert(payloads).execute()
            logger.info(f"Bulk inserted {len(payloads)} projects for profile {profile_id}")
                
        except Exception as e:
            logger.error(f"Error updating projects: {str(e)}")
            raise

    async def _update_certifications(self, profile_id: str, certifications: List[Dict[str, Any]]):
        """Update certifications for a profile using bulk insert"""
        try:
            # Delete existing certifications
            await self.supabase.client.table('certifications')\
                .delete()\
                .eq('profile_id', profile_id)\
                .execute()
            
            if not certifications:
                return

            # Prepare bulk insert payloads
            payloads = []
            for idx, cert in enumerate(certifications):
                payloads.append({
                    'profile_id': profile_id,
                    'name': cert.get('name'),
                    'issuing_organization': cert.get('issuing_organization'),
                    'issue_date': self._sanitize_date(cert.get('issue_date')),
                    'display_order': idx
                })
            
            # Bulk insert
            await self.supabase.client.table('certifications').insert(payloads).execute()
            logger.info(f"Bulk inserted {len(payloads)} certifications for profile {profile_id}")
                
        except Exception as e:
            logger.error(f"Error updating certifications: {str(e)}")
            raise
    
    async def add_education(self, profile_id: str, education: Dict[str, Any]) -> Dict[str, Any]:
        """Add a single education entry"""
        try:
            # Get current count for display_order
            count_response = await self.supabase.client.table('educations')\
                .select('id', count='exact')\
                .eq('profile_id', profile_id)\
                .execute()
            
            edu_payload = {
                'profile_id': profile_id,
                'degree': education.get('degree'),
                'field_of_study': education.get('field_of_study'),
                'institution': education.get('institution'),
                'location': education.get('location'),
                'graduation_date': self._sanitize_date(education.get('graduation_date')),
                'gpa': education.get('gpa'),
                'display_order': count_response.count or 0
            }
            
            response = await self.supabase.client.table('educations').insert(edu_payload).execute()
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Error adding education: {str(e)}")
            raise
    
    async def delete_education(self, education_id: str):
        """Delete an education entry"""
        try:
            await self.supabase.client.table('educations').delete().eq('id', education_id).execute()
        except Exception as e:
            logger.error(f"Error deleting education: {str(e)}")
            raise
    
    # ==================== Helper Methods ====================
    
    async def get_profile_completion_percentage(self, user_id: str) -> int:
        """Calculate profile completion percentage"""
        try:
            profile = await self.get_profile(user_id)
            if not profile:
                return 0
            
            score = 0
            total = 100
            
            # Basic info (40 points)
            if profile.get('full_name'): score += 10
            if profile.get('email'): score += 10
            if profile.get('phone'): score += 5
            if profile.get('city'): score += 5
            if profile.get('professional_summary'): score += 10
            
            # Work experience (30 points)
            if profile.get('work_experiences'):
                score += min(30, len(profile['work_experiences']) * 15)
            
            # Education (20 points)
            if profile.get('educations'):
                score += min(20, len(profile['educations']) * 10)
            
            # Skills (10 points)
            if profile.get('skills') and len(profile['skills']) > 0:
                score += 10
            
            return min(100, score)
            
        except Exception as e:
            logger.error(f"Error calculating profile completion: {str(e)}")
            return 0

    def _sanitize_date(self, date_str: Optional[str]) -> Optional[str]:
        """Convert YYYY-MM to YYYY-MM-DD for Postgres compatibility"""
        if not date_str:
            # Return None for missing dates (e.g. current job end_date)
            # Don't fabricate today's date - it corrupts is_current semantics
            return None
            
        # Clean up whitespace
        clean_date = str(date_str).strip()
        
        # Handle "null", "None", "Present", "Current" case-insensitively
        if not clean_date or clean_date.lower() in ['null', 'none', 'present', 'current']:
            return None
            
        # Handle YYYY-MM format
        if len(clean_date) == 7 and '-' in clean_date:
            try:
                # Validate it's actually YYYY-MM
                year, month = map(int, clean_date.split('-'))
                if 1900 <= year <= 2100:
                    return f"{clean_date}-01"
            except:
                pass

        # Smart Regex extraction for "2023 - Current" or "Jan 2023"
        import re
        # Look for YYYY-MM
        match_ym = re.search(r'(\d{4})-(\d{2})', clean_date)
        if match_ym:
             return f"{match_ym.group(1)}-{match_ym.group(2)}-01"
             
        # Look for just YYYY
        match_y = re.search(r'(\d{4})', clean_date)
        if match_y:
             year = int(match_y.group(1))
             if 1900 <= year <= 2100:
                 return f"{year}-01-01"

        # If all parsing fails, return None (which triggers fallback in caller or DB default)
        logger.warning(f"Could not parse date: '{date_str}', falling back to Today")
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')
