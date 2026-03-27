import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from backend.services.ai_service import AIService

@pytest.fixture
def ai_service():
    return AIService()

@pytest.mark.asyncio
async def test_ats_analysis_structure(ai_service):
    # Mocking the API call to return valid JSON
    mock_response = """
    ```json
    {
        "score": 85,
        "strengths": ["Good format"],
        "warnings": ["Typos"],
        "countrySpecific": ["Good photo"],
        "keywords": {"found": 5, "missing": []}
    }
    ```
    """
    
    with patch.object(ai_service, '_call_api', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = mock_response
        
        result = await ai_service.analyze_resume("Resume content", "Developer", "Germany")
        
        assert result["score"] == 85
        assert result["country"] == "Germany"
        assert len(result["strengths"]) == 1

@pytest.mark.asyncio
async def test_ats_failure_fallback(ai_service):
    # Mocking total failure (None return)
    
    with patch.object(ai_service, '_call_api', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = None
        
        result = await ai_service.analyze_resume("Resume content", "Developer", "India")
        
        # Should return fallback structure
        assert result["score"] == 0
        assert result["is_fallback"] is True
        assert "AI Service Unavailable" in result["warnings"][0]
        # Should still have country specific advice from static config
        assert len(result["countrySpecific"]) > 0

@pytest.mark.asyncio
async def test_json_cleaning(ai_service):
    dirty_json = """
    Here is the analysis:
    {
        "score": 90
    }
    Hope this helps!
    """
    cleaned = ai_service._clean_json_string(dirty_json)
    assert "90" in cleaned
    assert "Here is" not in cleaned
