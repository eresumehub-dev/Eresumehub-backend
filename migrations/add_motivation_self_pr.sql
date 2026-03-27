-- Migration: Add Motivation and Self-PR fields to user_profiles
-- Date: 2026-03-06

-- 1. Check if the columns exist first to avoid errors
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'user_profiles' AND column_name = 'motivation') THEN
        ALTER TABLE user_profiles ADD COLUMN motivation TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'user_profiles' AND column_name = 'self_pr') THEN
        ALTER TABLE user_profiles ADD COLUMN self_pr TEXT;
    END IF;
END $$;

COMMENT ON COLUMN user_profiles.motivation IS 'Specific motivation/reason for applying to roles (Required for Japan).';
COMMENT ON COLUMN user_profiles.self_pr IS 'Specific Japanese Self-PR content (Required for Japan).';
