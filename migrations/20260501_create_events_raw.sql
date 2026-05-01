-- Create events_raw table for GA-style behavioral tracking
CREATE TABLE IF NOT EXISTS public.events_raw (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id UUID REFERENCES auth.users(id),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    context JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Indexing for high-performance analytics
CREATE INDEX IF NOT EXISTS idx_events_raw_event_name ON public.events_raw (event_name);
CREATE INDEX IF NOT EXISTS idx_events_raw_user_id ON public.events_raw (user_id);
CREATE INDEX IF NOT EXISTS idx_events_raw_session_id ON public.events_raw (session_id);
CREATE INDEX IF NOT EXISTS idx_events_raw_timestamp ON public.events_raw (timestamp DESC);

-- Enable Row Level Security
ALTER TABLE public.events_raw ENABLE ROW LEVEL SECURITY;

-- Service role can do everything
CREATE POLICY "Service role can do everything" ON public.events_raw
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Allow authenticated users to view their own events (Optional)
CREATE POLICY "Users can view their own events" ON public.events_raw
    FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);
