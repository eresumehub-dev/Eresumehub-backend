import pytest
import asyncio
from unittest.mock import MagicMock, patch
from services.resume_pipeline import ResumePipeline, PipelineError

@pytest.mark.asyncio
async def test_compliance_bypass_logic():
    """
    Test that ignore_compliance=True correctly bypasses the PipelineError
    when mandatory fields are missing. (Fix for Issue #01)
    Tests interaction with ResumeComplianceValidator.
    """
    # 1. Setup Mock Dependencies
    mock_profile_service = MagicMock()
    mock_ai_service = MagicMock()
    mock_supabase = MagicMock()
    mock_analytics = MagicMock()
    
    user_data = {
        "full_name": "Test User",
        "email": "test@example.com"
    }
    
    # 2. Instantiate Pipeline
    pipeline = ResumePipeline(
        request_id="test-req",
        profile_service=mock_profile_service,
        ai_service=mock_ai_service,
        supabase_service=mock_supabase,
        analytics_service=mock_analytics
    )
    
    # 3. Patch Validator to return failure
    with patch('services.resume_pipeline.ResumeComplianceValidator.validate') as mock_validate:
        mock_validate.return_value = {
            "valid": False,
            "errors": [{"field": "phone", "message": "Phone is required"}]
        }
        
        # 3a. Verify FAILURE when ignore_compliance is False
        data_strict = {"country": "Germany", "ignore_compliance": False}
        with pytest.raises(PipelineError) as excinfo:
            await pipeline._step_validate(user_data, data_strict)
        
        mock_validate.assert_called_with(user_data, "Germany")
        assert excinfo.value.code == "COMPLIANCE_REQUIRED"
        assert "phone" in str(excinfo.value.message).lower()

        # 3b. Verify SUCCESS when ignore_compliance is True (THE FIX)
        mock_validate.reset_mock()
        data_bypass = {"country": "Germany", "ignore_compliance": True}
        country = await pipeline._step_validate(user_data, data_bypass)
        
        mock_validate.assert_called_once_with(user_data, "Germany")
        assert country == "Germany"
        assert pipeline.compliance_gap == ["phone"]
