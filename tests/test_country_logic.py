import pytest
from configurations.countries import get_country_context, get_country_fallback_data

def test_germany_context():
    context = get_country_context("Germany")
    assert "TARGET COUNTRY: Germany" in context
    assert "DATE FORMAT: DD.MM.YYYY" in context
    assert "MAX PAGES" in context

def test_usa_context():
    context = get_country_context("United States")
    assert "No Photo" in context
    assert "MM/YYYY" in context

def test_japan_date_format():
    # Verify Japan date format is YYYY.MM.DD (from RAG) not the old YYYY/MM/DD
    context = get_country_context("Japan")
    assert "DATE FORMAT: YYYY.MM.DD" in context

def test_fallback_logic():
    # Test unknown country falls back to United States/Global
    context = get_country_context("Mars")
    assert context is not None
    # Should contain some default structure from the US fallback
    assert "TARGET COUNTRY: Mars" in context
    assert "MAX PAGES" in context

def test_fallback_data_structure():
    data = get_country_fallback_data("India")
    assert "strengths" in data
    assert "warnings" in data
    assert "countrySpecific" in data
    assert len(data["countrySpecific"]) >= 3
