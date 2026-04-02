"""
User Profile Service
Handles CRUD operations for user profiles, work experiences, and education
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, date, timezone
from services.supabase_service import supabase_service
import logging

logger = logging.getLogger(__name__)


class ProfileService:
    def __init__(self, supabase_service):
        self.supabase = supabase_service
    
    # ==================== Profile CRUD ====================
    
    async def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user profile with work experiences and education (Using platform_user_id)"""
        try:
            # 1. Get profile using PLATFORM ID (Standard v3.16.0)
            profile_response = await self.supabase.client.table('user_profiles')\
                .select('*')\
                .eq('user_id', user_id)\
                .execute()
            
            # 2. Fallback check: Some legacy profiles might still use Auth ID string
            if not profile_response.data:
                logger.info(f"Profile not found by platform_id {user_id}, checking if it exists under Auth ID...")
                # We can perform a join if needed, but for now we look for the user record
                user_res = await self.supabase.client.table('users').select('auth_user_id').eq('id', user_id).execute()
                if user_res.data:
                    auth_id = user_res.data[0]['auth_user_id']
                    profile_response = await self.supabase.client.table('user_profiles')\
                        .select('*').eq('user_id', auth_id).execute()

            if not profile_response.data:
                return None
            
            profile = profile_response.data[0]
            profile_id = profile['id']
            
            # Get work experiences
            work_response = await self.supabase.client.table('work_experiences')\
                .select('*')\
                .eq('profile_id', profile_id)\
                .order('display_order')\
                .execute()
            
            # Get education
            edu_response = await self.supabase.client.table('educations')\
                .select('*')\
                .eq('profile_id', profile_id)\
                .order('display_order')\
                .execute()
            
            profile['work_experiences'] = work_response.data or []
            profile['educations'] = edu_response.data or []

            # Get projects
            try:
                proj_response = await self.supabase.client.table('projects')\
                    .select('*')\
                    .eq('profile_id', profile_id)\
                    .order('display_order')\
                    .execute()
                profile['projects'] = proj_response.data or []
            except Exception as e:
                logger.warning(f"Error fetching projects (table might be missing): {str(e)}")
                profile['projects'] = []
            
            # Get certifications
            try:
                cert_response = await self.supabase.client.table('certifications')\
                    .select('*')\
                    .eq('profile_id', profile_id)\
                    .order('display_order')\
                    .execute()
                profile['certifications'] = cert_response.data or []
            except Exception as e:
                logger.warning(f"Error fetching certifications (table might be missing): {str(e)}")
                profile['certifications'] = []
            
            # Get extras (awards, interests, etc.)
            try:
                extras_response = await self.supabase.client.table('profile_extras')\
                    .select('*')\
                    .eq('profile_id', profile_id)\
                    .execute()
                profile['extras'] = extras_response.data[0] if extras_response.data else {}
            except Exception as e:
                logger.warning(f"Error fetching extras (table might be missing): {str(e)}")
                profile['extras'] = {}
            
            return profile
            
        except Exception as e:
            logger.error(f"Error fetching profile for user {user_id}: {str(e)}")
            raise
    
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
                    await self._update_certifications(profile_id, profile_data['certifications'])
            
            return await self.get_profile(user_id)
            
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
