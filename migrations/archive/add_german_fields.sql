-- Migration to add German-specific compliance fields
ALTER TABLE public.user_profiles
ADD COLUMN IF NOT EXISTS street_address TEXT,
ADD COLUMN IF NOT EXISTS postal_code TEXT,
ADD COLUMN IF NOT EXISTS nationality TEXT,
ADD COLUMN IF NOT EXISTS date_of_birth DATE;

-- Comment for documentation
COMMENT ON COLUMN public.user_profiles.street_address IS 'User street address for German CV compliance.';
COMMENT ON COLUMN public.user_profiles.postal_code IS 'User postal code (PLZ) for German CV compliance.';
COMMENT ON COLUMN public.user_profiles.nationality IS 'User nationality for German CV compliance.';
COMMENT ON COLUMN public.user_profiles.date_of_birth IS 'User date of birth for German CV compliance.';

-- Add city/country to work_experiences
ALTER TABLE public.work_experiences
ADD COLUMN IF NOT EXISTS city TEXT,
ADD COLUMN IF NOT EXISTS country TEXT;

-- Add city/country to educations
ALTER TABLE public.educations
ADD COLUMN IF NOT EXISTS city TEXT,
ADD COLUMN IF NOT EXISTS country TEXT;
