import pytest
from configurations.countries import get_country_context, COUNTRY_RULES

def test_country_rules_exist():
    assert "Germany" in COUNTRY_RULES
    assert "India" in COUNTRY_RULES
    assert "USA" in COUNTRY_RULES
    assert "Japan" in COUNTRY_RULES

def test_germany_context():
    context = get_country_context("Germany")
    assert "CULTURAL NORMS" in context
    assert "photo_required" in str(COUNTRY_RULES["Germany"]["formatting"])
    assert "DD.MM.YYYY" in context

def test_usa_context():
    context = get_country_context("USA")
    assert "No Photo" in context
    assert "MM/YYYY" in context

def test_fallback_logic():
    # Test unknown country falls back to USA/Global
    context = get_country_context("Mars")
    assert context is not None
    # Should contain some default structure
    assert "FORMATTING" in context
