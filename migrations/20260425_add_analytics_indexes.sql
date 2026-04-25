-- 20260425_add_analytics_indexes.sql
-- Optimize analytics queries by indexing foreign keys (v16.5.1)

-- 1. Index on resume_id for dashboard view counts
CREATE INDEX IF NOT EXISTS idx_resume_views_resume_id ON resume_views(resume_id);

-- 2. Index on viewer_user_id for user activity tracking
CREATE INDEX IF NOT EXISTS idx_resume_views_viewer_user_id ON resume_views(viewer_user_id);

-- 3. Optional: Compound index for specific time-range lookups
CREATE INDEX IF NOT EXISTS idx_resume_views_created_at ON resume_views(created_at DESC);
