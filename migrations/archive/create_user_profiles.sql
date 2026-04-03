-- =====================================================
-- User Profile System for Automated Resume Generation
-- =====================================================
-- Run this in your Supabase SQL Editor

-- 1. Create user_profiles table
CREATE TABLE IF NOT EXISTS user_profiles (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE NOT NULL,
  full_name TEXT,
  email TEXT,
  phone TEXT,
  city TEXT,
  country TEXT,
  linkedin_url TEXT,
  photo_url TEXT,
  professional_summary TEXT,
  skills JSONB DEFAULT '[]',
  languages JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Create work_experiences table
CREATE TABLE IF NOT EXISTS work_experiences (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE NOT NULL,
  job_title TEXT NOT NULL,
  company TEXT NOT NULL,
  location TEXT,
  start_date DATE NOT NULL,
  end_date DATE,
  is_current BOOLEAN DEFAULT FALSE,
  achievements TEXT[] DEFAULT '{}',
  display_order INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Create educations table
CREATE TABLE IF NOT EXISTS educations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE NOT NULL,
  degree TEXT NOT NULL,
  field_of_study TEXT,
  institution TEXT NOT NULL,
  location TEXT,
  graduation_date DATE,
  gpa TEXT,
  display_order INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_work_experiences_profile_id ON work_experiences(profile_id);
CREATE INDEX IF NOT EXISTS idx_educations_profile_id ON educations(profile_id);

-- 5. Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 6. Add triggers for updated_at
DROP TRIGGER IF EXISTS update_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER update_user_profiles_updated_at
  BEFORE UPDATE ON user_profiles
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_work_experiences_updated_at ON work_experiences;
CREATE TRIGGER update_work_experiences_updated_at
  BEFORE UPDATE ON work_experiences
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_educations_updated_at ON educations;
CREATE TRIGGER update_educations_updated_at
  BEFORE UPDATE ON educations
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- 7. Enable Row Level Security (RLS)
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_experiences ENABLE ROW LEVEL SECURITY;
ALTER TABLE educations ENABLE ROW LEVEL SECURITY;

-- 8. Create RLS policies for user_profiles
DROP POLICY IF EXISTS "Users can view their own profile" ON user_profiles;
CREATE POLICY "Users can view their own profile"
  ON user_profiles FOR SELECT
  USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert their own profile" ON user_profiles;
CREATE POLICY "Users can insert their own profile"
  ON user_profiles FOR INSERT
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update their own profile" ON user_profiles;
CREATE POLICY "Users can update their own profile"
  ON user_profiles FOR UPDATE
  USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can delete their own profile" ON user_profiles;
CREATE POLICY "Users can delete their own profile"
  ON user_profiles FOR DELETE
  USING (auth.uid() = user_id);

-- 9. Create RLS policies for work_experiences
DROP POLICY IF EXISTS "Users can view their own work experiences" ON work_experiences;
CREATE POLICY "Users can view their own work experiences"
  ON work_experiences FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = work_experiences.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can insert their own work experiences" ON work_experiences;
CREATE POLICY "Users can insert their own work experiences"
  ON work_experiences FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = work_experiences.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can update their own work experiences" ON work_experiences;
CREATE POLICY "Users can update their own work experiences"
  ON work_experiences FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = work_experiences.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can delete their own work experiences" ON work_experiences;
CREATE POLICY "Users can delete their own work experiences"
  ON work_experiences FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = work_experiences.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

-- 10. Create RLS policies for educations
DROP POLICY IF EXISTS "Users can view their own educations" ON educations;
CREATE POLICY "Users can view their own educations"
  ON educations FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = educations.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can insert their own educations" ON educations;
CREATE POLICY "Users can insert their own educations"
  ON educations FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = educations.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can update their own educations" ON educations;
CREATE POLICY "Users can update their own educations"
  ON educations FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = educations.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can delete their own educations" ON educations;
CREATE POLICY "Users can delete their own educations"
  ON educations FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = educations.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

-- Done! Your profile system tables are ready.
