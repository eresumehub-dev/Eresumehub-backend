import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from services.resume_pipeline import ResumePipeline, PipelineError

@pytest.mark.asyncio
async def test_compliance_bypass_logic():
    """
    Test that ignore_compliance=True correctly bypasses the PipelineError
    when mandatory fields are missing. (Fix for Issue #01)
    """
    # 1. Setup Mock Dependencies
    mock_profile_service = MagicMock()
    mock_ai_service = MagicMock()
    mock_supabase = MagicMock()
    mock_analytics = MagicMock()
    
    # Simple user data missing 'phone' for a country that requires it
    user_data = {
        "full_name": "Test User",
        "email": "test@example.com"
        # Missing 'phone'
    }
    
    # Mock RAG data (required fields)
    from services.rag_rule_loader import rag_rule_loader
    rag_rule_loader.load_country_rules = MagicMock(return_value={
        "required_fields": ["phone"],
        "mandatory_sections": ["Experience"]
    })

    # 2. Instantiate Pipeline
    pipeline = ResumePipeline(
        request_id="test-req",
        profile_service=mock_profile_service,
        ai_service=mock_ai_service,
        supabase_service=mock_supabase,
        analytics_service=mock_analytics
    )
    
    # 3. Verify FAILURE when ignore_compliance is False
    data_strict = {"country": "Germany", "ignore_compliance": False}
    with pytest.raises(PipelineError) as excinfo:
        await pipeline._step_validate(user_data, data_strict)
    
    assert excinfo.value.code == "COMPLIANCE_REQUIRED"
    assert "phone" in excinfo.value.message

    # 4. Verify SUCCESS when ignore_compliance is True (THE FIX)
    data_bypass = {"country": "Germany", "ignore_compliance": True}
    country = await pipeline._step_validate(user_data, data_bypass)
    
    assert country == "Germany"
    assert pipeline.compliance_gap == ["phone"]
