ALTER TABLE public.user_profiles
ADD COLUMN IF NOT EXISTS links JSONB DEFAULT '[]',
ADD COLUMN IF NOT EXISTS headline TEXT;

-- 2. Create certifications table if missing
CREATE TABLE IF NOT EXISTS public.certifications (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES public.user_profiles(id) ON DELETE CASCADE NOT NULL,
  name TEXT NOT NULL,
  issuing_organization TEXT NOT NULL,
  issue_date DATE NOT NULL,
  expiration_date DATE,
  credential_id TEXT,
  credential_url TEXT,
  display_order INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Create profile_extras table if missing
CREATE TABLE IF NOT EXISTS public.profile_extras (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES public.user_profiles(id) ON DELETE CASCADE UNIQUE NOT NULL,
  publications JSONB DEFAULT '[]',
  volunteering JSONB DEFAULT '[]',
  awards JSONB DEFAULT '[]',
  interests JSONB DEFAULT '[]',
  "references" JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS for new tables
ALTER TABLE public.certifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profile_extras ENABLE ROW LEVEL SECURITY;

-- Simple RLS Policy (matches existing pattern)
DROP POLICY IF EXISTS "Public access" ON public.certifications;
CREATE POLICY "Public access" ON public.certifications FOR ALL USING (true);

DROP POLICY IF EXISTS "Public access" ON public.profile_extras;
CREATE POLICY "Public access" ON public.profile_extras FOR ALL USING (true);

-- Documentation
COMMENT ON COLUMN public.user_profiles.links IS 'Stores a list of personal links (e.g., Portfolio, Behance, GitHub) as a JSONB array of objects {label, url}.';
COMMENT ON COLUMN public.user_profiles.headline IS 'Professional headline or job title (e.g., Senior UI/UX Designer).';
