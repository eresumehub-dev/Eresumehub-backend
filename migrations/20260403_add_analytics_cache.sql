-- Migrations/20260403_add_analytics_cache.sql
-- Precomputed Analytics for Dashboard Performance (v15.0.0)

CREATE TABLE IF NOT EXISTS public.user_analytics_cache (
    user_id UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    dashboard_json JSONB NOT NULL,
    last_computed_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE public.user_analytics_cache ENABLE ROW LEVEL SECURITY;

-- RLS Policies
DROP POLICY IF EXISTS "Users can view their own analytics cache" ON public.user_analytics_cache;
CREATE POLICY "Users can view their own analytics cache" 
    ON public.user_analytics_cache FOR SELECT 
    USING (auth.uid() IN (SELECT auth_user_id FROM public.users WHERE id = user_id));

-- Updated_at trigger
DROP TRIGGER IF EXISTS update_analytics_cache_updated_at ON public.user_analytics_cache;
CREATE TRIGGER update_analytics_cache_updated_at
    BEFORE UPDATE ON public.user_analytics_cache
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Indexes for lightning fast lookups
CREATE INDEX IF NOT EXISTS idx_analytics_cache_updated_at ON public.user_analytics_cache(updated_at DESC);
