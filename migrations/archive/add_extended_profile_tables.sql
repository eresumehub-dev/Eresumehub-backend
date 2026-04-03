-- =====================================================
-- Extended Profile System - Projects, Certifications, Extras
-- =====================================================
-- Run this in your Supabase SQL Editor AFTER create_user_profiles.sql

-- 1. Create projects table
CREATE TABLE IF NOT EXISTS projects (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  technologies JSONB DEFAULT '[]',
  link TEXT,
  role TEXT,
  start_date DATE,
  end_date DATE,
  display_order INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Create certifications table
CREATE TABLE IF NOT EXISTS certifications (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE NOT NULL,
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

-- 3. Create profile_extras table (for optional sections)
CREATE TABLE IF NOT EXISTS profile_extras (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE UNIQUE NOT NULL,
  publications JSONB DEFAULT '[]',
  volunteering JSONB DEFAULT '[]',
  awards JSONB DEFAULT '[]',
  interests JSONB DEFAULT '[]',
  references JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_projects_profile_id ON projects(profile_id);
CREATE INDEX IF NOT EXISTS idx_certifications_profile_id ON certifications(profile_id);
CREATE INDEX IF NOT EXISTS idx_profile_extras_profile_id ON profile_extras(profile_id);

-- 5. Add triggers for updated_at
DROP TRIGGER IF EXISTS update_projects_updated_at ON projects;
CREATE TRIGGER update_projects_updated_at
  BEFORE UPDATE ON projects
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_certifications_updated_at ON certifications;
CREATE TRIGGER update_certifications_updated_at
  BEFORE UPDATE ON certifications
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_profile_extras_updated_at ON profile_extras;
CREATE TRIGGER update_profile_extras_updated_at
  BEFORE UPDATE ON profile_extras
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- 6. Enable Row Level Security (RLS)
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE certifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE profile_extras ENABLE ROW LEVEL SECURITY;

-- 7. Create RLS policies for projects
DROP POLICY IF EXISTS "Users can view their own projects" ON projects;
CREATE POLICY "Users can view their own projects"
  ON projects FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = projects.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can insert their own projects" ON projects;
CREATE POLICY "Users can insert their own projects"
  ON projects FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = projects.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can update their own projects" ON projects;
CREATE POLICY "Users can update their own projects"
  ON projects FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = projects.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can delete their own projects" ON projects;
CREATE POLICY "Users can delete their own projects"
  ON projects FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = projects.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

-- 8. Create RLS policies for certifications
DROP POLICY IF EXISTS "Users can view their own certifications" ON certifications;
CREATE POLICY "Users can view their own certifications"
  ON certifications FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = certifications.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can insert their own certifications" ON certifications;
CREATE POLICY "Users can insert their own certifications"
  ON certifications FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = certifications.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can update their own certifications" ON certifications;
CREATE POLICY "Users can update their own certifications"
  ON certifications FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = certifications.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can delete their own certifications" ON certifications;
CREATE POLICY "Users can delete their own certifications"
  ON certifications FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = certifications.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

-- 9. Create RLS policies for profile_extras
DROP POLICY IF EXISTS "Users can view their own extras" ON profile_extras;
CREATE POLICY "Users can view their own extras"
  ON profile_extras FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = profile_extras.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can insert their own extras" ON profile_extras;
CREATE POLICY "Users can insert their own extras"
  ON profile_extras FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = profile_extras.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can update their own extras" ON profile_extras;
CREATE POLICY "Users can update their own extras"
  ON profile_extras FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = profile_extras.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can delete their own extras" ON profile_extras;
CREATE POLICY "Users can delete their own extras"
  ON profile_extras FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE user_profiles.id = profile_extras.profile_id
      AND user_profiles.user_id = auth.uid()
    )
  );

-- Done! Extended profile tables are ready.
