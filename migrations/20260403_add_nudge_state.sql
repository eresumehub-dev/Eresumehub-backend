-- Migrations/20260403_add_nudge_state.sql
-- Persistence for the Real-Time Trigger Engine (v14.0.0)

CREATE TABLE IF NOT EXISTS public.user_nudge_state (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
    resume_id UUID REFERENCES public.resumes(id) ON DELETE CASCADE,
    nudge_type VARCHAR(50) NOT NULL, -- 'weak_hook', 'discovery_friction', 'conversion_leak'
    status VARCHAR(20) DEFAULT 'seen' CHECK (status IN ('seen', 'dismissed', 'acted')),
    confidence_at_trigger NUMERIC,
    triggered_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE public.user_nudge_state ENABLE ROW LEVEL SECURITY;

-- RLS Policies
DROP POLICY IF EXISTS "Users can view their own nudge states" ON public.user_nudge_state;
CREATE POLICY "Users can view their own nudge states" 
    ON public.user_nudge_state FOR SELECT 
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update their own nudge states" ON public.user_nudge_state;
CREATE POLICY "Users can update their own nudge states" 
    ON public.user_nudge_state FOR ALL 
    USING (auth.uid() = user_id);

-- Updated_at trigger
DROP TRIGGER IF EXISTS update_nudge_state_updated_at ON public.user_nudge_state;
CREATE TRIGGER update_nudge_state_updated_at
    BEFORE UPDATE ON public.user_nudge_state
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_nudge_state_user_id ON public.user_nudge_state(user_id);
CREATE INDEX IF NOT EXISTS idx_nudge_state_resume_id ON public.user_nudge_state(resume_id);
