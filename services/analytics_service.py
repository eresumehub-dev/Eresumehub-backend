"""
Analytics Service
Handles data processing and insights generation using pandas
"""
import orjson
import logging
import pandas as pd
import asyncio
import re
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class AnalyticsService:
    # We no longer need in-memory TTLCache as we use the DB cache table (v15.0.0)

    def __init__(self, supabase_service):
        self.supabase = supabase_service

    @classmethod
    def invalidate_user_cache(cls, user_id: str):
        """Invalidate the DB cache and enqueue a refresh (v15.1.0)"""
        # Note: We'll call this from mutations. It triggers an async background recompute.
        cls.enqueue_refresh(user_id)
        logger.info(f"ANALYTICS Cache Invalidation Enqueued for user {user_id}")

    @staticmethod
    def enqueue_refresh(user_id: str):
        """
        Deterministic Background Refresh with Throttling (v15.2.0)
        Prevents job 'spamming' (e.g. 1 refresh per 60s per user).
        """
        from services.cache_service import cache_service
        lock_key = f"refresh_lock_analytics:{user_id}"
        
        # 1. Attempt to acquire the 'recompute lock' (60s throttle)
        # If the key exists, we SKIP the enqueue to save CPU/Memory
        # v16.4.5 Async Alignment
        async def _check_and_refresh():
            if not await cache_service.set_nx(lock_key, "locked", ttl_seconds=60):
                logger.info(f"ANALYTICS Refresh Skipped: User {user_id} is within the 60s throttle window.")
                return

            # 2. Fire-and-forget the heavy lift
            from services.supabase_service import supabase_service
            service = AnalyticsService(supabase_service)
            # Create task to handle the actual refresh
            asyncio.create_task(service.refresh_user_analytics_cache(user_id))
            logger.info(f"ANALYTICS Refresh Task Dispatched for user {user_id}")

        # Since enqueue_refresh is a staticmethod and sometimes called from sync code,
        # we check the event loop state.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_check_and_refresh())
        except RuntimeError:
            # Fallback if no loop (unlikely in FastAPI but good for scripts)
            asyncio.run(_check_and_refresh())

    @classmethod
    def clear_full_analytics_cache(cls):
        """Global Clear for system-wide updates (v10.0.0)"""
        logger.info("Global Analytics Cache Cleared")

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
        [STRICT V16.0.0] Canonical Read-Only: Fetches precomputed analytics from the cache table.
        Standardized on auth_user_id. O(1) DB Read.
        """
        try:
            response = await self.supabase.client.table("user_analytics_cache")\
                .select("dashboard_json")\
                .eq("user_id", user_id)\
                .execute()
            
            if response.data and response.data[0].get('dashboard_json'):
                return response.data[0]['dashboard_json']
            
            # PRO-GRADE: Return empty/stale state instead of blocking.
            logger.info(f"Analytics cache MISS for user {user_id}. Returning default state.")
            return self._get_empty_analytics(0)
            
        except Exception as e:
            logger.error(f"Error fetching analytics cache: {e}")
            return self._get_empty_analytics(0)

    async def refresh_user_analytics_cache(self, user_id: str) -> Dict[str, Any]:
        """
        The 'Heavy' Computation Engine (v15.0.0)
        Moved out of the critical login path. This can be run in background.
        Performs Pandas vectorization, behavioral modeling, and heuristic audits.
        """
        try:
            logger.info(f"Refreshing analytics cache for user {user_id}...")
            # 1. Fetch all resumes for this user
            resumes = await self.supabase.get_user_resumes(user_id)
            if not resumes:
                empty = self._get_empty_analytics(0)
                await self._save_to_cache(user_id, empty)
                return empty

            # 2. Base Power Score (Content-Level)
            analyzed_resumes = [r for r in resumes if r.get('resume_data', {}).get('score', 0) > 0]
            avg_base_score = 0
            if analyzed_resumes:
                avg_base_score = sum(r['resume_data']['score'] for r in analyzed_resumes) / len(analyzed_resumes)

            resume_ids = [r['id'] for r in resumes]
            
            # 3. CONCURRENT MULTI-STREAM FETCH
            results = await asyncio.gather(
                self.supabase.client.table("resume_views").select("*").in_("resume_id", resume_ids).execute(),
                self.supabase.client.table("resume_downloads").select("*").in_("resume_id", resume_ids).execute(),
                self.supabase.client.table("events_raw").select("*").filter("properties->>resume_id", "in", f"({','.join(resume_ids)})").execute(),
                return_exceptions=True
            )

            legacy_views = results[0].data if not isinstance(results[0], Exception) else []
            legacy_downloads = results[1].data if not isinstance(results[1], Exception) else []
            raw_events = results[2].data if not isinstance(results[2], Exception) else []

            # 4. DATA UNIFICATION & ENRICHMENT
            events_df = pd.DataFrame(raw_events)
            unified_views = []
            
            for lv in legacy_views:
                unified_views.append({
                    **lv, "source": "legacy", "engagement_score": 0.3
                })
            
            interact_counts = {}
            ttv_map = {}
            
            if not events_df.empty:
                interactions = events_df[events_df['event_name'] == 'content_interaction']
                for _, e in interactions.iterrows():
                    key = (e['session_id'], e.get('properties', {}).get('resume_id'))
                    interact_counts[key] = interact_counts.get(key, 0) + 1
                
                starts = events_df[events_df['event_name'] == 'resume_view_started']
                for _, e in starts.iterrows():
                    props = e.get('properties', {})
                    ctx = e.get('context', {})
                    unified_views.append({
                        "id": e['event_id'], "resume_id": props.get('resume_id'),
                        "session_id": e['session_id'], "viewed_at": e['timestamp'],
                        "visitor_country": ctx.get('country') or "Unknown",
                        "device_type": ctx.get('device_type') or "desktop",
                        "referrer": ctx.get('referrer') or "Direct",
                        "duration_seconds": 0, "max_scroll_depth": 0, "source": "v13_event"
                    })
                
                heartbeats = events_df[events_df['event_name'] == 'resume_view_heartbeat']
                if not heartbeats.empty:
                    heartbeats['resume_id'] = heartbeats['properties'].apply(lambda x: x.get('resume_id'))
                    heartbeats['duration'] = heartbeats['properties'].apply(lambda x: x.get('total_duration', 0))
                    heartbeats['scroll'] = heartbeats['properties'].apply(lambda x: x.get('scroll_depth', 0))
                    heartbeats['ttv'] = heartbeats['properties'].apply(lambda x: x.get('time_to_first_scroll'))
                    
                    hb_agg = heartbeats.groupby(['session_id', 'resume_id']).agg({
                        'duration': 'max', 'scroll': 'max', 'ttv': 'min'
                    })
                    
                    view_map = { (v.get('session_id'), v.get('resume_id')): i for i, v in enumerate(unified_views) }
                    for (sid, rid), row in hb_agg.iterrows():
                        v_idx = view_map.get((sid, rid))
                        if v_idx is not None:
                            unified_views[v_idx]['duration_seconds'] = row['duration']
                            unified_views[v_idx]['max_scroll_depth'] = row['scroll']
                            if not pd.isna(row['ttv']): ttv_map[(sid, rid)] = row['ttv']

            views_df = pd.DataFrame(unified_views)
            
            # 5. PROBABILISTIC ENGAGEMENT SCORING
            def calculate_engagement(row):
                dur = row.get('duration_seconds', 0) or 0
                scroll = row.get('max_scroll_depth', 0) or 0
                sid_rid = (row.get('session_id'), row.get('resume_id'))
                interactions = interact_counts.get(sid_rid, 0)
                score = (min(1.0, dur / 120) * 0.4) + (min(1.0, scroll) * 0.3) + (min(1.0, interactions / 5) * 0.2) + 0.1
                return round(score, 2)

            if not views_df.empty:
                views_df['engagement_score'] = views_df.apply(calculate_engagement, axis=1)
                views_df['is_engaged'] = views_df['engagement_score'] > 0.4

            # 6. MULTI-DIMENSIONAL SEGMENTATION
            segments = {
                "device": views_df.groupby('device_type')['engagement_score'].mean().to_dict() if not views_df.empty else {},
                "referrer": {},
                "ttu_median": float(pd.Series(list(ttv_map.values())).median()) if ttv_map else 0
            }
            if not views_df.empty and 'referrer' in views_df.columns:
                segments["referrer"] = views_df.groupby('referrer')['engagement_score'].mean().to_dict()

            # 7. PREDICTIVE MODEL V1: SUCCESS PROBABILITY
            success_predictions = {}
            for rid in resume_ids:
                rv = views_df[views_df['resume_id'] == rid] if not views_df.empty else pd.DataFrame()
                if len(rv) > 5:
                    base_ps = next((r['resume_data']['score'] for r in resumes if r['id'] == rid), 0)
                    engage_ratio = rv['is_engaged'].mean()
                    prob = (base_ps/100 * 0.4) + (engage_ratio * 0.4) + (min(5, interact_counts.get(rid, 0))/5 * 0.2)
                    success_predictions[rid] = min(0.99, round(prob, 2))

            # 8. AUTO-DIAGNOSIS ENGINE
            target_pool = [r for r in resumes if r.get('resume_data')]
            all_recs = []
            for res in target_pool:
                rec = self._get_fallback_recommendation(res.get('resume_data', {}), res.get('title'))
                if rec:
                    rec["resume_id"] = res['id']
                    rv = views_df[views_df['resume_id'] == res['id']] if not views_df.empty else pd.DataFrame()
                    if not rv.empty and len(rv) > 10:
                        ttv_vals = [ttv_map.get((row['session_id'], res['id'])) for _, row in rv.iterrows() if (row['session_id'], res['id']) in ttv_map]
                        avg_ttv = sum(ttv_vals)/len(ttv_vals) if ttv_vals else 0
                        if avg_ttv > 3.0:
                            rec["fix"] = { "title": "Discovery Friction", "suggested": "Relocate Summary to Top", "points": 40 }
                        elif rv['engagement_score'].mean() < 0.3:
                            rec["fix"] = { "title": "Weak Narrative Hook", "suggested": "Add Quantified Impact", "points": 45 }
                    all_recs.append(rec)

            best_rec = sorted(all_recs, key=lambda x: x['fix'].get('points', 0), reverse=True)[0] if all_recs else None

            # 9. ASSEMBLE FAANG-GRADE SUMMARY
            total_views = len(views_df)
            total_downloads = len(legacy_downloads) + (len(events_df[events_df['event_name'] == 'resume_download']) if not events_df.empty else 0)
            
            avg_time = views_df['duration_seconds'].median() if not views_df.empty else 0
            avg_time = 0 if pd.isna(avg_time) else float(round(avg_time, 1))

            p_score = views_df['engagement_score'].mean() if not views_df.empty else 0
            p_score = 0 if pd.isna(p_score) else int(p_score * 100)

            analytics = {
                "summary": {
                    "total_views": total_views, "engaged_views": int(views_df['is_engaged'].sum()) if not views_df.empty else 0,
                    "unique_viewers": int(views_df['session_id'].nunique()) if not views_df.empty else 0,
                    "total_downloads": total_downloads,
                    "avg_time_spent": avg_time,
                    "ttv_median": segments["ttu_median"],
                    "conversion_rate": round((total_downloads / max(1, total_views)) * 100, 1),
                    "power_score": p_score,
                    "total_resumes": len(resumes)
                },
                "segments": segments,
                "funnel": { "views": total_views, "engagement": int(views_df['is_engaged'].sum()) if not views_df.empty else 0, "downloads": total_downloads },
                "recommendation": best_rec or self._get_empty_analytics(0)['recommendation'],
                "resume_performance": [],
                "activities": [],
                "nudges": [],
                "last_computed": datetime.now(timezone.utc).isoformat()
            }

            # 9.5 ENRICH WITH SYNC NUDGES (v15.1.0 Unified Cache)
            analytics['nudges'] = await self.get_active_nudges_from_data(analytics, user_id)
            
            # Map activities for timeline
            if not views_df.empty:
                activities = []
                import uuid
                for _, row in views_df.sort_values('viewed_at', ascending=False).head(20).iterrows():
                    res_title = next((r['title'] for r in resumes if r['id'] == row['resume_id']), "Resume")
                    activities.append({
                        "id": row.get('id', str(uuid.uuid4())),
                        "event_name": "resume_view",
                        "timestamp": row['viewed_at'],
                        "resume_title": res_title,
                        "country": row.get('visitor_country')
                    })
                analytics['activities'] = activities

            for r in resumes:
                rv = views_df[views_df['resume_id'] == r['id']] if not views_df.empty else pd.DataFrame()
                e_score = rv['engagement_score'].mean() if not rv.empty else 0
                e_score = 0 if pd.isna(e_score) else round(e_score, 2)
                
                analytics['resume_performance'].append({
                    "id": r['id'], "title": r['title'], "views": len(rv),
                    "engagement_score": e_score,
                    "success_probability": success_predictions.get(r['id'], 0.1),
                    "downloads": len(rv[rv['source'] == 'legacy']) if not rv.empty and 'source' in rv.columns else 0,
                    "insight_tag": "Trending" if len(rv) > 10 and e_score > 0.6 else "Stable"
                })

            if not views_df.empty:
                views_df['viewed_at_dt'] = pd.to_datetime(views_df['viewed_at']).dt.tz_localize(None)
                idx = pd.date_range(end=datetime.now(), periods=30, freq='D').normalize()
                analytics['trends'] = [{"viewed_at": d.strftime("%Y-%m-%d"), "views": int(c)} for d, c in views_df.set_index('viewed_at_dt').resample('D').size().reindex(idx, fill_value=0).items()]
                counts_df = views_df['device_type'].value_counts().reset_index()
                if 'index' in counts_df.columns:
                    analytics['device_stats'] = counts_df.rename(columns={'index':'device', 'device_type':'count'}).to_dict('records')
                else:
                    analytics['device_stats'] = counts_df.rename(columns={'device_type':'device', 'count':'count'}).to_dict('records')
                analytics['geo_distribution'] = views_df['visitor_country'].value_counts().head(10).reset_index().rename(columns={'index':'country', 'visitor_country':'visitors', 'count':'visitors', 'visitor_country':'country'}).to_dict('records')

            # 10. SAVE TO DB CACHE
            analytics = self._convert_numpy(analytics)
            await self._save_to_cache(user_id, analytics)
            return analytics

        except Exception as e:
            logger.error(f"Intelligence Engine Failure: {str(e)}", exc_info=True)
            return self._get_empty_analytics(0)

    async def get_active_nudges_from_data(self, analytics: Dict[str, Any], user_id: str) -> List[Dict[str, Any]]:
        """
        Unified Logic: Extracts nudges using precomputed analytics data (v15.1.0).
        Used during both background recompute and nudge endpoints.
        """
        try:
            results = await asyncio.gather(
                self._calculate_global_benchmarks(),
                self.supabase.get_user_nudge_states(user_id)
            )
            bench = results[0]
            nudge_history = results[1]
            
            dismissed = { (n.get('resume_id'), n.get('nudge_type')) for n in nudge_history if n.get('status') == 'dismissed' }
            nudges = []
            resumes_perf = analytics.get('resume_performance', [])
            summary = analytics.get('summary', {})

            for r in resumes_perf:
                rid = r['id']
                views = r['views']
                if views < 5: continue

                ttv = r.get('ttv_median', summary.get('ttv_median', 0))
                engagement = r.get('engagement_score', 0)
                
                if (rid, 'weak_hook') not in dismissed:
                    if ttv > (bench['ttv_median'] * 1.5) and engagement < bench['engagement_median']:
                        nudges.append({
                            "type": "weak_hook", "resume_id": rid, "resume_title": r['title'],
                            "title": "Your Narrative Hook is Dragging",
                            "message": f"Readers are pausing {round(ttv, 1)}s before scrolling.",
                            "confidence": min(0.95, views / 30), "action": "Relocate Summary", "impact": "🔥 High impact"
                        })
            # Final Prioritization (Impact x Confidence)
            nudges.sort(key=lambda x: x.get('confidence', 0), reverse=True)
            return nudges[:2] # Only show top 2 to avoid fatigue
        except Exception as e:
            logger.error(f"Nudge Aggregation Failure: {e}")
            return []

        except Exception as e:
            logger.error(f"Trigger Engine Failure: {e}")
            return []

    def _get_empty_analytics(self, power_score=0):
        return {
            "summary": {
                "total_views": 0, "total_downloads": 0, "avg_time_spent": 0, 
                "conversion_rate": 0, "power_score": power_score,
                "total_resumes": 0, "analyzed_resumes": 0
            },
            "trends": [], "activities": [], "resume_performance": [],
            "geo_distribution": [], "device_stats": [],
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

    async def _save_to_cache(self, user_id: str, analytics: Dict[str, Any]):
        """Persist computed analytics to the database cache table (v15.0.0)"""
        try:
            # We use upsert on user_id to keep a single canonical cache record
            await self.supabase.client.table("user_analytics_cache").upsert({
                "user_id": user_id,
                "dashboard_json": analytics,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).execute()
            logger.info(f"Analytics cache saved for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to save analytics cache: {e}")

    async def _calculate_global_benchmarks(self) -> Dict[str, float]:
        """Fetch or calculate global engagement benchmarks for nudges (v15.1.0)"""
        # FUTURE: This should query an aggregation view across all users.
        # For now, we return high-end industry standards for "professional" feedback.
        return {
            "ttv_median": 2.5,        # 2.5s to first scroll is standard
            "engagement_median": 0.35, # 35% engagement is healthy
            "conversion_median": 8.0   # 8% conversion to download
        }

# Instance must be created dynamically to prevent module-level DB connections.
