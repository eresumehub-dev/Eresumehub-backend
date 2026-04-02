"""
Analytics Service
Handles data processing and insights generation using pandas
"""
import pandas as pd
import logging
import random
import re
import orjson
from typing import Dict, List, Any
from datetime import datetime, timedelta
from cachetools import TTLCache

logger = logging.getLogger(__name__)

class AnalyticsService:
    def __init__(self, supabase_service):
        self.supabase = supabase_service
        self._dashboard_cache = TTLCache(maxsize=100, ttl=300) # 5-minute cache

    def _get_fallback_recommendation(self, resume_data, resume_title="Resume"):
        """
        Returns a deterministic 'Forensic Tip' based on actual content patterns.
        Replaces legacy random selection.
        """
        # FEATURE FLAG: Heuristic Audit is RE-ENABLED (v7.0)
        return {
            "resume_title": resume_title,
            "resume_id": None,
            "fix": self._run_heuristic_audit(resume_data, resume_title)
        }
        
        # return {
        #     "resume_title": resume_title,
        #     "resume_id": None,
        #     "fix": self._run_heuristic_audit(resume_data, resume_title)
        # }

    def _run_heuristic_audit(self, resume_data, resume_title="Resume"):
        """
        Runs a 5-Level Heuristic Audit on resume content.
        Returns a specific 'fix' object based on the first failure found.
        """
        
        # Helper to safely get text content
        def get_text_content(data):
            text = ""
            if not data: return ""
            # From sections array
            if 'sections' in data:
                for s in data['sections']:
                    if isinstance(s.get('content'), str):
                        text += " " + s['content']
                    elif isinstance(s.get('content'), list):
                        # Handle list of items (e.g. experience dicts)
                        for item in s['content']:
                            if isinstance(item, dict):
                                text += " " + str(item.get('description', ''))
                            elif isinstance(item, str):
                                text += " " + item
            return text.lower()

        full_text = get_text_content(resume_data)
        
        # --- LEVEL 1: THE INVISIBLE CHECK (Digital Footprint) ---
        # Scans for links to LinkedIn, GitHub, or Portfolio
        has_links = False
        link_patterns = [r'linkedin\.com', r'github\.com', r'behance\.net', r'dribbble\.com', r'medium\.com', r'http']
        
        # 1. Check Top-Level Keys (FLAT SCHEMA)
        if resume_data.get('linkedin_url'): has_links = True
        if resume_data.get('website'): has_links = True
        if resume_data.get('portfolio_url'): has_links = True

        # 2. Structural Check in 'contact' dict
        if 'contact' in resume_data and isinstance(resume_data['contact'], dict):
            if resume_data['contact'].get('linkedin'): has_links = True
            if resume_data['contact'].get('website'): has_links = True
        
        # 3. JSON Resume standard 'basics'
        if not has_links and 'basics' in resume_data:
             for profile in resume_data['basics'].get('profiles', []):
                 if isinstance(profile, dict) and profile.get('url'):
                     has_links = True
        
        # 4. Sections scan (Legacy/Section-based Schema)
        if hasattr(resume_data, 'get') and 'sections' in resume_data:
             for s in resume_data['sections']:
                 stype = str(s.get('type', '')).lower()
                 if stype in ['personal_details', 'contact']:
                     contact_str = str(s.get('content', '')).lower()
                     if any(re.search(p, contact_str) for p in link_patterns):
                         has_links = True
        
        # 5. Full text fallback
        if not has_links and re.search(r'(https?://|www\.|linkedin\.com)', full_text):
            has_links = True

        if not has_links:
            return {
                "title": "Invisible Profile Detected",
                "current": "No professional links found",
                "suggested": "Add LinkedIn or Portfolio URL",
                "reasoning": "Recruiters expect immediate access to your digital proof. Missing links reduce callback rates by 40%.",
                "points": 15
            }

        # --- LEVEL 2: THE VAGUE CLAIMS CHECK (Metrics) ---
        exp_text = ""
        
        # 1. FLAT SCHEMA Extraction (work_experiences)
        # Check plural and singular variations
        flat_lists = [
            resume_data.get('work_experiences'), 
            resume_data.get('work_experience'), 
            resume_data.get('experience'), 
            resume_data.get('employment_history')
        ]
        
        for lst in flat_lists:
            if isinstance(lst, list):
                for item in lst:
                    if isinstance(item, dict):
                         # Try description, summary, or bullets
                         exp_text += " " + str(item.get('description', ''))
                         exp_text += " " + str(item.get('summary', ''))
        
        # 2. Sections based extraction
        if 'sections' in resume_data:
            excluded_types = ['education', 'projects', 'skills', 'languages', 'personal_details', 'contact', 'summary', 'profile']
            for s in resume_data['sections']:
                stype = str(s.get('type', '')).lower()
                is_explicit_exp = stype in ['experience', 'work_experience', 'employment']
                
                if is_explicit_exp or (stype not in excluded_types and 'experience' in stype):
                    if isinstance(s.get('content'), list):
                        for item in s.get('content'):
                            if isinstance(item, dict):
                                exp_text += " " + str(item.get('description', ''))
                    elif isinstance(s.get('content'), str):
                        exp_text += " " + s['content']
        
        logger.info(f"Heuristic Debug: Final Exp Text Len: {len(exp_text)}")
        
        # Strict Metric Regex: Matches %, $, +, or numbers with specific impact words
        # avoiding dates (2020, 01/02)
        metric_patterns = [
            r'\d+%',                       # 20%
            r'[\$\£\€]\s?\d+',             # $500
            r'\d+\+',                      # 50+
            r'\d+\s(users|clients|customers|revenue|budget|saved|reduced|increased|staff|team|members|downloads)'  # Contextual
        ]
        
        metrics_found = 0
        for pattern in metric_patterns:
            metrics_found += len(re.findall(pattern, exp_text, re.IGNORECASE))
        
        if metrics_found < 2 and len(exp_text) > 100: # Only if they have experience
            # DYNAMIC ADVICE GENERATION
            role_advice = self._get_role_based_advice(resume_title, "metrics")
            
            return {
                "title": "Quantify Your Impact",
                "current": "General duties described",
                "suggested": f"Add metrics (e.g. {role_advice})",
                "reasoning": "Vague claims are ignored. Numbers prove your value. Aim for at least 2 hard metrics (%, $, or 10+ items).",
                "points": 20
            }

        # --- LEVEL 3: THE PASSIVE DOER CHECK (Power Verbs) ---
        weak_verbs = ['responsible for', 'worked on', 'helped', 'assisted', 'duties included', 'participated in']
        weak_count = 0
        found_weak = ""
        for verb in weak_verbs:
            if verb in exp_text.lower():
                weak_count += 1
                found_weak = verb
                
        if weak_count > 0:
            role_advice = self._get_role_based_advice(resume_title, "verbs")
            return {
                "title": "Weak Verbs Detected",
                "current": f"Used passive phrasing like '{found_weak}'",
                "suggested": f"Use Power Verbs (e.g. {role_advice})",
                "reasoning": "Passive language makes you look like a participant, not a driver. Start every bullet with a Power Verb.",
                "points": 10
            }

        # --- LEVEL 4: THE TASK LIST CHECK (Bullet Length) ---
        # If bullets are too short (< 40 chars), they are likely just tasks
        short_bullets = 0
        total_bullets = 0
        if 'sections' in resume_data:
            for s in resume_data['sections']:
                if s.get('type') in ['experience', 'work_experience']:
                     if isinstance(s.get('content'), list):
                        for item in s.get('content'):
                             if isinstance(item, dict):
                                 desc = str(item.get('description', ''))
                                 # Split by newlines or bullets
                                 lines = re.split(r'\n|•|-', desc)
                                 for line in lines:
                                     if len(line.strip()) > 5: # Valid bullet
                                         total_bullets += 1
                                         if len(line.strip()) < 40:
                                             short_bullets += 1
        
        if total_bullets > 3 and (short_bullets / total_bullets) > 0.6: # Increased threshold
             return {
                "title": "Expand on Results",
                "current": "Bullet points are too short (Tasks)",
                "suggested": "Use 'Action + Result' format",
                "reasoning": "You listed duties. Employers hire for Results. Expand bullets to explain the 'So What?' of your work.",
                "points": 10
            }

        # --- LEVEL 5: THE EXECUTIVE VISION CHECK (God Tier) ---
        # For high-quality resumes, check for Strategic positioning in Summary
        summary_text = ""
        
        # 1. Flat Schema Check
        if resume_data.get('professional_summary'):
            summary_text += str(resume_data.get('professional_summary'))
        if resume_data.get('summary'):
            summary_text += " " + str(resume_data.get('summary'))
            
        # 2. Sections Schema Check
        if 'sections' in resume_data:
            for s in resume_data['sections']:
                stype = str(s.get('type', '')).lower()
                if stype in ['summary', 'profile', 'professional_summary']:
                    summary_text += " " + str(s.get('content', '')).lower()
        
        summary_text = summary_text.lower()
        logger.info(f"Heuristic Debug: Summary Len: {len(summary_text)}")
        
        # Stricter list for Executive Check
        strategic_keywords = ['strategic', 'revenue', 'roadmap', 'architecture', 'vision', 'orchestrated', 'spearheaded', 'growth', 'scale', 'stakeholder', 'transformation']
        found_strategic = sum(1 for k in strategic_keywords if k in summary_text)
        
        # Must have at least 3 to pass "God Tier"
        if found_strategic < 3 and len(summary_text) > 50:
             return {
                "title": "Elevate to Executive Level",
                "current": "Operational focus in Summary",
                "suggested": "Inject 3+ Strategic Keywords",
                "reasoning": "You have strong skills. Now signal leadership potential by using words like 'Strategic Roadmap', 'P&L', or 'Revenue Growth'.",
                "points": 25
            }

        # --- FALLBACK: THE PERFECT RESUME ---
        return {
            "title": "Resume is Optimized",
            "current": "No critical forensic errors found",
            "suggested": "Prepare for Interviews",
            "reasoning": "Your resume passed all forensic scans. Focus on your interview prep now.",
            "points": 0
        }

    def _get_role_based_advice(self, title, check_type):
        """
        Returns context-aware advice based on the job title.
        This makes the forensic card feel 'smart' without using an LLM.
        """
        title = title.lower()
        
        # 1. Define Role Archetypes
        is_dev = any(k in title for k in ['developer', 'engineer', 'stack', 'software', 'tech', 'data', 'architect'])
        is_sales = any(k in title for k in ['sales', 'account', 'business', 'growth', 'executive', 'representative'])
        is_manager = any(k in title for k in ['manager', 'lead', 'director', 'head', 'vp', 'chief'])
        is_support = any(k in title for k in ['admin', 'support', 'assistant', 'clerk', 'receptionist', 'coordinator'])
        is_marketing = any(k in title for k in ['marketing', 'social', 'brand', 'content', 'seo', 'media'])
        
        # 2. Return Specific Advice
        if check_type == "metrics":
            if is_dev: return "'Reduced latency by 20%', 'Scaled to 10k users'"
            if is_sales: return "'Exceeded quota by 15%', 'Generated $50k'"
            if is_marketing: return "'Increased engagement by 30%', 'Doubled traffic'"
            if is_support: return "'Processed 50+ daily requests', '0 errors'"
            if is_manager: return "'Led team of 15', 'Reduced costs by 10%'"
            return "'Managed $50k budget', '20% growth'" # Default
            
        if check_type == "verbs":
            if is_dev: return "'Engineered', 'Optimized', 'Deployed'"
            if is_sales: return "'Closed', 'Negotiated', 'Generated'"
            if is_marketing: return "'Launched', 'Captivated', 'Analyzed'"
            if is_support: return "'Orchestrated', 'Resolved', 'Streamlined'"
            if is_manager: return "'Spearheaded', 'Mentored', 'Directed'"
            return "'Spearheaded', 'Engineered'" # Default
            
        return ""

    async def get_dashboard_analytics(self, user_id: str) -> Dict[str, Any]:
        """
        Generate comprehensive analytics for the user dashboard.
        Uses pandas to aggregate views, downloads, and interaction metrics.
        """
        if user_id in self._dashboard_cache:
            return self._dashboard_cache[user_id]
            
        try:
            # 1. Fetch all resumes for this user
            resumes = await self.supabase.get_user_resumes(user_id)
            
            # Weighted Power Score Calculation (v7.0)
            # Power Score = (Average Resume Score * 0.7) + (View Velocity * 0.3)
            # View velocity measures how 'hot' the resumes are in the market
            analyzed_resumes = [r for r in resumes if r.get('resume_data', {}).get('score', 0) > 0]
            
            # Weighted average calculation
            if analyzed_resumes:
                avg_base_score = sum(r['resume_data']['score'] for r in analyzed_resumes) / len(analyzed_resumes)
                # Velocity will be added after traffic processing below
                power_score = round(avg_base_score)
            else:
                power_score = 0

            if not resumes:
                return self._get_empty_analytics(power_score)

            resume_ids = [r['id'] for r in resumes]
            
            # 2. Fetch raw events (views and downloads) - Filter by last 30 days
            cutoff_date = (datetime.now() - timedelta(days=30)).isoformat()
            
            views_response = await self.supabase.client.table("resume_views")\
                .select("*")\
                .in_("resume_id", resume_ids)\
                .gte("viewed_at", cutoff_date)\
                .execute()
            
            downloads_response = await self.supabase.client.table("resume_downloads")\
                .select("*")\
                .in_("resume_id", resume_ids)\
                .gte("downloaded_at", cutoff_date)\
                .execute()

            views_df = pd.DataFrame(views_response.data)
            downloads_df = pd.DataFrame(downloads_response.data)

            # 3. Process with Pandas
            
            # Fetch recent activities for the dashboard timeline
            activities = []
            
            # Get 10 most recent views
            recent_views = views_df.sort_values('viewed_at', ascending=False).head(10) if not views_df.empty else pd.DataFrame()
            if not recent_views.empty:
                for _, view in recent_views.iterrows():
                    resume_title = next((r['title'] for r in resumes if r['id'] == view['resume_id']), "Unknown Resume")
                    
                    # Session Quality (Engagement Bucketing)
                    duration = view.get('duration_seconds', 0)
                    engagement = "bot_likely"
                    if duration > 5: engagement = "skimmed"
                    if duration > 30: engagement = "engaged"
                    if duration > 120: engagement = "deep_read"
                    
                    activities.append({
                        "id": str(view['id']),
                        "type": "view",
                        "resume_id": view['resume_id'],
                        "resume_title": resume_title,
                        "timestamp": view['viewed_at'],
                        "country": view.get('visitor_country', 'Unknown'),
                        "browser": view.get('browser', 'Unknown'),
                        "duration": duration,
                        "engagement": engagement
                    })
            
            # Get 10 most recent downloads
            recent_downloads = downloads_df.sort_values('downloaded_at', ascending=False).head(10) if not downloads_df.empty else pd.DataFrame()
            for _, dl in recent_downloads.iterrows():
                resume_title = next((r['title'] for r in resumes if r['id'] == dl['resume_id']), "Unknown Resume")
                activities.append({
                    "id": str(dl['id']),
                    "type": "download",
                    "resume_id": dl['resume_id'],
                    "resume_title": resume_title,
                    "timestamp": dl['downloaded_at'],
                    "country": dl.get('visitor_country', 'Unknown'),
                    "device": dl.get('device_type', 'Unknown')
                })
                
            # Sort all activities by timestamp descending (using pandas for reliability)
            if activities:
                activities_df = pd.DataFrame(activities)
                activities_df['timestamp'] = pd.to_datetime(activities_df['timestamp'])
                activities_df = activities_df.sort_values('timestamp', ascending=False)
                activities = activities_df.head(15).to_dict('records')
            else:
                activities = []

            # Basic Counts
            total_views = len(views_df)
            total_downloads = len(downloads_df)

            # Weighted Power Score refinement with traffic
            if analyzed_resumes and total_views > 0:
                view_velocity = min(30, (total_views / len(analyzed_resumes)) * 5) # Cap at 30 points
                power_score = round((power_score * 0.7) + view_velocity)
            
            # Unique Viewers based on session_id
            unique_viewers = 0
            if not views_df.empty:
                if 'session_id' in views_df.columns:
                    # Filter out null sessions and count unique
                    unique_viewers = int(views_df[views_df['session_id'].notna()]['session_id'].nunique())
                    # If some are null, we might treat each null as a unique viewer or just count non-null
                    # For safety, add the count of rows with null session_id if they exist
                    null_sessions_count = int(views_df['session_id'].isna().sum())
                    unique_viewers += null_sessions_count
                else:
                    # Fallback to total views if column missing (shouldn't happen)
                    unique_viewers = total_views

            # Initialize response structure
            analytics = {
                "summary": {
                    "total_views": total_views,
                    "unique_viewers": unique_viewers,
                    "total_downloads": total_downloads,
                    "avg_time_spent": 0,
                    "conversion_rate": 0,
                    "power_score": power_score, # precise power score
                    "total_resumes": len(resumes),
                    "analyzed_resumes": len(analyzed_resumes)
                },
                "trends": [],
                "geo_distribution": [],
                "device_stats": [],
                "resume_performance": [],
                "activities": activities
            }

            # --- Recommendation Engine (MOVED UP) ---
            # Calculates forensic tip regardless of traffic data
            
            # 1. Select a Target Resume
            target_resume = None
            if analyzed_resumes:
                target_resume = min(analyzed_resumes, key=lambda r: r['resume_data'].get('score', 100))
            elif resumes:
                target_resume = resumes[0]
            
            # DEBUG LOGGING for Heuristics
            if target_resume:
                rd_debug = target_resume.get('resume_data', {})
                logger.info(f"Heuristic Debug: Analyze Resume '{target_resume.get('title')}' (ID: {target_resume.get('id')})")
                logger.info(f"Heuristic Debug: Data Keys: {list(rd_debug.keys())}")
                if 'sections' in rd_debug:
                     logger.info(f"Heuristic Debug: Sections found: {len(rd_debug['sections'])}")
                else:
                     logger.info("Heuristic Debug: NO 'sections' KEY FOUND")

            # 2. Run Universal Content Scanner
            resume_data = target_resume.get('resume_data', {}) if target_resume else {}
            resume_title = target_resume.get('title', "Resume") if target_resume else "Resume"
            
            recommendation = self._get_fallback_recommendation(resume_data, resume_title)
            
            if recommendation:
                if target_resume:
                    recommendation["resume_id"] = target_resume['id']
                
                # 3. Optimization Override (Use AI 'Top Fix')
                if target_resume and 'top_fix' in resume_data:
                     top_fix = resume_data['top_fix']
                     if (isinstance(top_fix, dict) and 
                         isinstance(top_fix.get('title'), str) and len(top_fix.get('title', '')) > 0 and
                         isinstance(top_fix.get('suggested'), str) and len(top_fix.get('suggested', '')) > 0):
                          recommendation["fix"] = top_fix

            analytics['recommendation'] = recommendation

            if views_df.empty:
                # Still return list of resumes even if no views
                analytics['resume_performance'] = [
                    {
                        'title': r['title'], 
                        'views': 0, 
                        'downloads': 0, 
                        'score': r.get('resume_data', {}).get('score', 0)
                    } 
                    for r in resumes
                ]
                # Default empty trends
                analytics['trends'] = [{"created_at": datetime.now().strftime("%Y-%m-%d"), "views": 0}] 
                
                # Convert and return EARLY (with recommendation now included!)
                return self._convert_numpy(analytics)

            # --- Time Spent Analysis ---
            if 'duration_seconds' in views_df.columns:
                # Filter out outliers (> 30 mins) and nulls
                valid_durations = views_df[
                    (views_df['duration_seconds'].notna()) & 
                    (views_df['duration_seconds'] > 0) & 
                    (views_df['duration_seconds'] < 1800)
                ]
                mean_time = valid_durations['duration_seconds'].mean()
                analytics['summary']['avg_time_spent'] = float(round(mean_time, 1)) if not valid_durations.empty else 0

            # --- Conversion Rate (Downloads / Views) ---
            if total_views > 0:
                analytics['summary']['conversion_rate'] = round((total_downloads / total_views) * 100, 1)

            # --- Trends (Last 30 Days) ---
            if not views_df.empty:
                views_df['viewed_at'] = pd.to_datetime(views_df['viewed_at'])
                daily_views = views_df.resample('D', on='viewed_at').size().reset_index(name='views')
                # Fill missing days
                analytics['trends'] = daily_views.tail(30).to_dict('records')

            # --- Geographic Distribution ---
            if 'visitor_country' in views_df.columns:
                geo_counts = views_df['visitor_country'].value_counts().reset_index()
                geo_counts.columns = ['country', 'visitors']
                analytics['geo_distribution'] = geo_counts.head(10).to_dict('records')

            # --- Device Stats ---
            if 'device_type' in views_df.columns:
                device_counts = views_df['device_type'].value_counts().reset_index()
                device_counts.columns = ['device', 'count']
                analytics['device_stats'] = device_counts.to_dict('records')

            # --- Per Resume Performance ---
            if not views_df.empty:
                # Group by resume and count total views + unique sessions + avg duration
                resume_stats = views_df.groupby('resume_id').agg(
                    views=('id', 'count'),
                    unique_viewers=('session_id', 'nunique') if 'session_id' in views_df.columns else ('id', 'count'),
                    avg_time=('duration_seconds', 'mean') if 'duration_seconds' in views_df.columns else ('id', lambda x: 0)
                ).reset_index()
                
                # Round avg_time
                resume_stats['avg_time'] = resume_stats['avg_time'].fillna(0).round(1)

                if not downloads_df.empty:
                    dl_stats = downloads_df.groupby('resume_id').size().reset_index(name='downloads')
                    resume_stats = pd.merge(resume_stats, dl_stats, on='resume_id', how='left').fillna(0)
                else:
                    resume_stats['downloads'] = 0

                # Merge with resume titles and scores
                resumes_df = pd.DataFrame(resumes)[['id', 'title', 'resume_data']]
                # Extract score safely
                resumes_df['score'] = resumes_df['resume_data'].apply(lambda x: x.get('score', 0) if isinstance(x, dict) else 0)
                
                performance_df = pd.merge(resumes_df, resume_stats, left_on='id', right_on='resume_id', how='left').fillna(0)
                
                # Sort by views, then score
                cols = ['id', 'title', 'views', 'unique_viewers', 'downloads', 'score', 'avg_time']
                performance_df = performance_df[cols].sort_values(['views', 'score'], ascending=False)
                
                # Convert Numpy types to native Python types
                analytics['resume_performance'] = performance_df.to_dict('records')
                for record in analytics['resume_performance']:
                    record['views'] = int(record['views'])
                    record['unique_viewers'] = int(record['unique_viewers'])
                    record['downloads'] = int(record['downloads'])
                    record['avg_time'] = float(record['avg_time'])
                    # score can be float, keep as is or cast
            else:
                analytics['resume_performance'] = [
                    {'id': r['id'], 'title': r['title'], 'views': 0, 'unique_viewers': 0, 'downloads': 0, 'score': r.get('resume_data', {}).get('score', 0), 'avg_time': 0} 
                    for r in resumes
                ]

            # Recommendation Engine logic moved up to run before traffic check
            # analytics['recommendation'] is already set above
            
            # Final Safety: Serialize to optimized JSON format then back to dict for generic handler
            # or return as raw bytes if the main router supports it
            raw_json = orjson.dumps(analytics, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_PASSTHROUGH_DATETIME)
            final_payload = orjson.loads(raw_json)
            
            self._dashboard_cache[user_id] = final_payload
            logger.info(f"DASHBOARD DEBUG for {user_id}: Rec Fix Title: {final_payload.get('recommendation', {}).get('fix', {}).get('title')}")
            return final_payload

        except Exception as e:
            logger.error(f"Error generating dashboard analytics: {str(e)}")
            return self._get_empty_analytics()

    def _get_empty_analytics(self, power_score=0):
        return {
            "summary": {
                "total_views": 0, 
                "total_downloads": 0, 
                "avg_time_spent": 0, 
                "conversion_rate": 0,
                "power_score": power_score
            },
            "trends": [],
            "geo_distribution": [],
            "device_stats": [],
            "resume_performance": [],
            "activities": [],
            "recommendation": self._get_fallback_recommendation({}, "Resume")
        }

    def _convert_numpy(self, obj):
        """Recursively convert numpy types to native Python types for JSON serialization"""
        import numpy as np
        
        if isinstance(obj, dict):
            return {k: self._convert_numpy(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_numpy(v) for v in obj]
        elif isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif pd.isna(obj):  # Handle NaN/None
            return None
        return obj
