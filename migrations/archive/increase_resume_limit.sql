-- Increase Resume Limit to 50
-- This script updates the check_resume_limit function to allow more resumes.

CREATE OR REPLACE FUNCTION public.check_resume_limit()
RETURNS TRIGGER AS $$
BEGIN
  -- Increase limit from 10 to 50
  IF (SELECT count(*) FROM public.resumes WHERE user_id = NEW.user_id) >= 50 THEN
    RAISE EXCEPTION 'Resume limit reached. Maximum 50 resumes allowed.' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Ensure Trigger exists (if not already)
DROP TRIGGER IF EXISTS enforce_resume_limit ON public.resumes;

CREATE TRIGGER enforce_resume_limit
BEFORE INSERT ON public.resumes
FOR EACH ROW
EXECUTE FUNCTION public.check_resume_limit();
