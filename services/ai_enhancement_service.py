"""
AI Enhancement Service
Enhances profile data using Gemini AI
"""
from typing import Dict, Any, List
import logging
import google.generativeai as genai
import os

logger = logging.getLogger(__name__)


class AIEnhancementService:
    def __init__(self):
        # Configure Gemini
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            logger.warning("GEMINI_API_KEY not found. AI enhancement will be disabled.")
            self.model = None
        else:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-pro')
    
    async def enhance_profile(self, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enhance entire profile with AI
        - Improves professional summary
        - Enhances bullet points with PAR format
        - Suggests additional skills
        """
        if not self.model:
            logger.warning("AI enhancement skipped - no API key configured")
            return profile_data
        
        enhanced = profile_data.copy()
        
        try:
            # Enhance professional summary
            if profile_data.get('professional_summary'):
                enhanced['professional_summary'] = await self.enhance_summary(
                    profile_data['professional_summary']
                )
            
            # Enhance work experiences
            if profile_data.get('work_experiences'):
                enhanced['work_experiences'] = await self.enhance_experiences(
                    profile_data['work_experiences']
                )
            
            # Extract and suggest skills
            if profile_data.get('work_experiences'):
                suggested_skills = await self.extract_skills_from_experience(
                    profile_data['work_experiences']
                )
                existing_skills = set(profile_data.get('skills', []) or [])
                all_skills = list(existing_skills.union(set(suggested_skills)))
                enhanced['skills'] = all_skills
            
            return enhanced
            
        except Exception as e:
            logger.error(f"Error enhancing profile: {str(e)}")
            # Return original data if enhancement fails
            return profile_data
    
    async def enhance_summary(self, summary: str) -> str:
        """
        Rewrite professional summary to be more impactful
        """
        if not self.model or not summary or len(summary.strip()) < 10:
            return summary
        
        try:
            prompt = f"""
Rewrite this professional summary to be more powerful and impactful for a resume.

Original: {summary}

Requirements:
- Keep it 2-3 sentences
- Highlight key strengths
- Use professional tone
- Make it action-oriented and results-focused
- Include quantifiable experience if mentioned
- Remove filler words

Return ONLY the improved summary, no explanations or quotes.
"""
            
            response = await self.model.generate_content_async(prompt)
            enhanced = response.text.strip().strip('"').strip("'")
            
            # Fallback to original if AI response is too short or invalid
            if len(enhanced) < 20:
                return summary
            
            return enhanced
            
        except Exception as e:
            logger.error(f"Error enhancing summary: {str(e)}")
            return summary
    
    async def enhance_bullet_point(self, bullet: str, job_title: str, company: str) -> str:
        """
        Transform a single bullet point into PAR/STAR format
        """
        if not self.model or not bullet or len(bullet.strip()) < 5:
            return bullet
        
        try:
            prompt = f"""
Transform this work achievement into a powerful resume bullet point.

Context: {job_title} at {company}
Original: {bullet}

Requirements:
- Use PAR format (Problem-Action-Result) or STAR format
- Start with a strong action verb
- Include quantifiable metrics if possible (even estimates like "~20%", "10+ users", etc.)
- Be specific and concise (1-2 lines max)
- Use past tense
- ATS-friendly keywords

Return ONLY the improved bullet point, no explanations or quotes.
"""
            
            response = await self.model.generate_content_async(prompt)
            enhanced = response.text.strip().strip('"').strip("'").strip('• ').strip('- ')
            
            # Ensure bullet starts with action verb
            if enhanced and enhanced[0].islower():
                enhanced = enhanced[0].upper() + enhanced[1:]
            
            # Fallback to original if too short
            if len(enhanced) < 10:
                return bullet
            
            return enhanced
            
        except Exception as e:
            logger.error(f"Error enhancing bullet point: {str(e)}")
            return bullet
    
    async def enhance_experiences(self, experiences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enhance all work experience bullet points
        """
        if not self.model:
            return experiences
        
        enhanced_experiences = []
        
        for exp in experiences:
            enhanced_exp = exp.copy()
            
            if exp.get('achievements') and isinstance(exp['achievements'], list):
                enhanced_achievements = []
                
                for achievement in exp['achievements']:
                    if achievement and isinstance(achievement, str) and len(achievement.strip()) > 0:
                        try:
                            enhanced = await self.enhance_bullet_point(
                                achievement,
                                exp.get('job_title', 'Professional'),
                                exp.get('company', 'Company')
                            )
                            enhanced_achievements.append(enhanced)
                        except Exception as e:
                            logger.error(f"Error enhancing bullet: {str(e)}")
                            enhanced_achievements.append(achievement)
                
                if enhanced_achievements:
                    enhanced_exp['achievements'] = enhanced_achievements
            
            enhanced_experiences.append(enhanced_exp)
        
        return enhanced_experiences
    
    async def extract_skills_from_experience(self, experiences: List[Dict[str, Any]]) -> List[str]:
        """
        Extract and suggest skills based on work experience
        """
        if not self.model or not experiences:
            return []
        
        try:
            # Build context from experiences
            context = []
            for exp in experiences:
                job_title = exp.get('job_title', '')
                company = exp.get('company', '')
                achievements = exp.get('achievements', [])
                context.append(f"{job_title} at {company}: {' '.join(achievements[:2])}")
            
            context_str = '\n'.join(context[:3])  # Limit to 3 most recent
            
            prompt = f"""
Based on this work experience, suggest 5-8 relevant professional skills that this person likely has.

Work Experience:
{context_str}

Return ONLY a comma-separated list of skills, no explanations.
Examples: Python, Leadership, Project Management, AWS, Agile
"""
            
            response = await self.model.generate_content_async(prompt)
            skills_text = response.text.strip()
            
            # Parse skills
            skills = [s.strip() for s in skills_text.split(',')]
            skills = [s for s in skills if s and len(s) < 30]  # Filter invalid
            
            return skills[:8]  # Max 8 suggestions
            
        except Exception as e:
            logger.error(f"Error extracting skills: {str(e)}")
            return []
    
    async def generate_achievement(self, job_title: str, company: str, context: str = "") -> str:
        """
        Generate a professional achievement bullet point from minimal context
        """
        if not self.model:
            return ""
        
        try:
            prompt = f"""
Generate a professional achievement bullet point for a resume.

Job Title: {job_title}
Company: {company}
Context: {context if context else "General responsibilities"}

Requirements:
- PAR or STAR format
- Start with action verb
- Include quantifiable result or impact
- Professional tone
- 1-2 lines max

Return ONLY the bullet point, no explanations.
"""
            
            response = await self.model.generate_content_async(prompt)
            bullet = response.text.strip().strip('"').strip("'").strip('• ').strip('- ')
            
            return bullet
            
        except Exception as e:
            logger.error(f"Error generating achievement: {str(e)}")
            return ""


# Global instance
ai_enhancement_service = AIEnhancementService()
