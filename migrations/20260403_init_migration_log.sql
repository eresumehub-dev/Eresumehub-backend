-- Migrations/20260403_init_migration_log.sql
-- Migration Tracking: Initialization (v16.1.0)

BEGIN;

CREATE TABLE IF NOT EXISTS public.migrations_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    migration_name TEXT UNIQUE NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    applied_by TEXT DEFAULT 'manual'
);

-- RLS (Restrict access to view only)
ALTER TABLE public.migrations_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Anyone can view migration logs" ON public.migrations_log;
CREATE POLICY "Anyone can view migration logs" ON public.migrations_log FOR SELECT USING (true);

-- Seed with initial entry
INSERT INTO public.migrations_log (migration_name, applied_by) 
VALUES ('20260403_init_migration_log.sql', 'manual_seed')
ON CONFLICT (migration_name) DO NOTHING;

COMMIT;

-- IMPORTANT: This migration should be run in Supabase SQL Editor.
