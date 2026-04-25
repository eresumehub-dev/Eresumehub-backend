-- 20260425_rpc_set_default.sql
-- Atomic toggle for default resume (v16.5.0)

CREATE OR REPLACE FUNCTION set_default_resume(target_user_id UUID, target_resume_id UUID)
RETURNS VOID AS $$
BEGIN
    -- 1. Unset existing default
    UPDATE resumes 
    SET is_default = FALSE 
    WHERE user_id = target_user_id 
    AND is_default = TRUE;

    -- 2. Set new default (strictly for the requested ID and owner)
    UPDATE resumes 
    SET is_default = TRUE 
    WHERE id = target_resume_id 
    AND user_id = target_user_id;
END;
$$ LANGUAGE plpgsql;
