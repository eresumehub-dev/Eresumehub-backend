-- ==========================================
--  ELITE PERFORMANCE INDEXES (v9.0.0)
--  Optimizes the 'Golden Bootstrap' Orchestrator
-- ==========================================

-- 1. Profile Retrieval Optimization
CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_work_experiences_profile_id ON work_experiences(profile_id);
CREATE INDEX IF NOT EXISTS idx_educations_profile_id ON educations(profile_id);

-- 2. Resume & Analytics Optimization
CREATE INDEX IF NOT EXISTS idx_resumes_user_id ON resumes(user_id);
CREATE INDEX IF NOT EXISTS idx_resume_views_resume_id ON resume_views(resume_id);
CREATE INDEX IF NOT EXISTS idx_resume_downloads_resume_id ON resume_downloads(resume_id);

-- 3. Concurrent Search Acceleration
CREATE INDEX IF NOT EXISTS idx_resumes_created_at_desc ON resumes(created_at DESC);
