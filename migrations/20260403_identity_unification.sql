-- Migrations/20260403_identity_unification.sql
-- Identity Unification: Standardizing on auth_user_id (v16.0.0)

BEGIN;

-- 1. Unify User Profiles
-- Convert any user_profiles.user_id = public.users.id to users.auth_user_id
UPDATE public.user_profiles
SET user_id = users.auth_user_id
FROM public.users
WHERE user_profiles.user_id = users.id
AND user_profiles.user_id != users.auth_user_id;

-- Enforce Uniqueness and RLS-ready ID
ALTER TABLE public.user_profiles DROP CONSTRAINT IF EXISTS user_profiles_user_id_key;
ALTER TABLE public.user_profiles ADD CONSTRAINT user_profiles_user_id_key UNIQUE (user_id);

-- 2. Unify Resumes
-- Convert any resumes.user_id = public.users.id to users.auth_user_id
UPDATE public.resumes
SET user_id = users.auth_user_id
FROM public.users
WHERE resumes.user_id = users.id
AND resumes.user_id != users.auth_user_id;

-- 3. Unify Analytics Cache
-- Convert any user_analytics_cache.user_id = public.users.id to users.auth_user_id
UPDATE public.user_analytics_cache
SET user_id = users.auth_user_id
FROM public.users
WHERE user_analytics_cache.user_id = users.id
AND user_analytics_cache.user_id != users.auth_user_id;

-- 4. Unify Latency Logs
-- Ensure all latency logs are keyed by canonical ID
UPDATE public.endpoint_latency_logs
SET user_id = users.auth_user_id
FROM public.users
WHERE endpoint_latency_logs.user_id = users.id
AND endpoint_latency_logs.user_id != users.auth_user_id;

-- 5. Unify Resume Views (Behavioral Analytics)
UPDATE public.resume_views
SET viewer_user_id = users.auth_user_id
FROM public.users
WHERE resume_views.viewer_user_id = users.id
AND resume_views.viewer_user_id != users.auth_user_id;

-- 6. Constraints: Reinforce Integrity
-- We make the 'user_id' in profiles a hard reference to the auth layer if possible
-- (Note: Foreign keys to auth.users require specialized permissions in Supabase, 
--  so we'll stick to RLS and uniqueness constraints as our primary guardrails).

-- 7. Audit Logging (v16.1.0)
INSERT INTO public.migrations_log (migration_name, applied_by) 
VALUES ('20260403_identity_unification.sql', 'manual_execution')
ON CONFLICT (migration_name) DO NOTHING;

COMMIT;

-- IMPORTANT: This migration should be run in the Supabase SQL Editor.
