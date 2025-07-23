from alasio.ext.locale.accept_language import negotiate_accept_language, negotiate_locale, parse_accept_language


class TestParseAcceptLanguage:
    """Test cases for the parse_accept_language function."""

    def test_empty_header(self):
        """Test parsing an empty or None header returns empty list."""
        assert parse_accept_language("") == []
        assert parse_accept_language(None) == []

    def test_single_language_no_quality(self):
        """Test parsing a single language without quality factor."""
        assert parse_accept_language("en") == ["en"]
        assert parse_accept_language("fr-CA") == ["fr-CA"]

    def test_single_language_with_quality(self):
        """Test parsing a single language with quality factor."""
        assert parse_accept_language("en;q=0.8") == ["en"]
        assert parse_accept_language("fr-CA;q=0.9") == ["fr-CA"]

    def test_multiple_languages_sorted_by_quality(self):
        """Test parsing multiple languages sorted by quality factor in descending order."""
        # Languages should be sorted by quality (highest first)
        result = parse_accept_language("fr;q=0.9, en;q=0.8, de;q=1.0")
        assert result == ["de", "fr", "en"]

        # Default quality is 1.0, so it should come first
        result = parse_accept_language("fr;q=0.9, en, de;q=0.8")
        assert result == ["en", "fr", "de"]

    def test_complex_header_from_docstring(self):
        """Test the example from the function docstring."""
        result = parse_accept_language("fr-CH, fr;q=0.9, en;q=0.8")
        assert result == ["fr-CH", "fr", "en"]

    def test_whitespace_handling(self):
        """Test that whitespace around languages and quality factors is handled properly."""
        result = parse_accept_language(" fr-CH , fr ; q = 0.9 , en ; q = 0.8 ")
        assert result == ["fr-CH", "fr", "en"]

    def test_invalid_quality_factors(self):
        """Test handling of invalid quality factors (should default to 1.0)."""
        # Invalid quality factor should default to 1.0
        result = parse_accept_language("en;q=invalid, fr;q=0.5")
        assert result == ["en", "fr"]  # en comes first due to default q=1.0

        # Empty quality factor
        result = parse_accept_language("en;q=, fr;q=0.5")
        assert result == ["en", "fr"]

        # Missing quality value
        result = parse_accept_language("en;q, fr;q=0.5")
        assert result == ["en", "fr"]

    def test_quality_boundary_values(self):
        """Test quality factors at boundary values."""
        result = parse_accept_language("en;q=0.0, fr;q=1.0")
        assert result == ["fr", "en"]

        result = parse_accept_language("en;q=0.001, fr;q=0.999")
        assert result == ["fr", "en"]

    def test_same_quality_factors(self):
        """Test languages with the same quality factor maintain original order."""
        result = parse_accept_language("en;q=0.8, fr;q=0.8, de;q=0.8")
        assert result == ["en", "fr", "de"]  # Should maintain original order

    def test_malformed_entries(self):
        """Test handling of malformed entries in the header."""
        # Empty language code should be ignored
        result = parse_accept_language(";q=0.8, fr, en;q=0.5")
        assert result == ["fr", "en"]

        # Multiple semicolons
        result = parse_accept_language("en;;q=0.8, fr")
        assert result == ["fr", "en"]

    def test_complex_realistic_headers(self):
        """Test realistic complex Accept-Language headers."""
        # Chrome-like header
        result = parse_accept_language("en-US,en;q=0.9,fr;q=0.8,fr-FR;q=0.7,es;q=0.6")
        assert result == ["en-US", "en", "fr", "fr-FR", "es"]

        # Firefox-like header
        result = parse_accept_language("en-US,en;q=0.5")
        assert result == ["en-US", "en"]


class TestNegotiateLocale:
    """Test cases for the negotiate_locale function."""

    def test_empty_inputs(self):
        """Test behavior with empty preferred or available lists."""
        assert negotiate_locale([], ["en", "fr"]) == ""
        assert negotiate_locale(["en"], []) == ""
        assert negotiate_locale([], []) == ""

    def test_direct_exact_match(self):
        """Test direct exact matching of locales."""
        available = ["en-US", "fr-FR", "de-DE"]

        # Exact match
        assert negotiate_locale(["en-US"], available) == "en-US"
        assert negotiate_locale(["fr-FR"], available) == "fr-FR"

        # First match wins when multiple preferences match
        assert negotiate_locale(["fr-FR", "en-US"], available) == "fr-FR"

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        available = ["en-US", "fr-FR"]

        assert negotiate_locale(["EN-US"], available) == "en-US"
        assert negotiate_locale(["en-us"], available) == "en-US"
        assert negotiate_locale(["Fr-Fr"], available) == "fr-FR"

    def test_separator_normalization(self):
        """Test that underscores and hyphens are normalized for matching."""
        available = ["en_US", "fr_CA", "zh-Hans"]

        # Hyphen in preferred, underscore in available
        assert negotiate_locale(["en-US"], available) == "en_US"

        # Underscore in preferred, hyphen in available
        assert negotiate_locale(["zh_Hans"], available) == "zh-Hans"

    def test_alias_matching(self):
        """Test matching using language aliases."""
        available = ["en-US", "nb-NO", "zh-CN"]

        # Test some common aliases from the aliases dict
        assert negotiate_locale(["en"], available) == "en-US"  # 'en' -> 'en-US'
        assert negotiate_locale(["no"], available) == "nb-NO"  # 'no' -> 'nb-NO'
        assert negotiate_locale(["zh"], available) == "zh-CN"  # 'zh' -> 'zh-CN'

    def test_fallback_matching(self):
        """Test fallback to primary language when specific variant not available."""
        available = ["en", "fr", "de"]

        # Should fallback from en-US to en
        assert negotiate_locale(["en-US"], available) == "en"
        assert negotiate_locale(["fr-CA"], available) == "fr"
        assert negotiate_locale(["de-AT"], available) == "de"

    def test_matching_priority_order(self):
        """Test that matching follows the correct priority: direct > alias > fallback."""
        # Test direct match takes precedence over alias
        available = ["en", "en-US"]
        assert negotiate_locale(["en"], available) == "en"  # Direct match, not alias to en-US

        # Test alias takes precedence over fallback
        available = ["en-US", "no"]
        # 'no' should match alias 'nb-NO' -> normalized 'nb-no', but since that's not available,
        # it should try fallback, but 'no' is available directly
        assert negotiate_locale(["no"], available) == "no"

    def test_preference_order_respected(self):
        """Test that the first matching preference is returned."""
        available = ["en-US", "fr-FR", "de-DE"]

        # First preference should win
        assert negotiate_locale(["fr-FR", "en-US"], available) == "fr-FR"
        assert negotiate_locale(["de-DE", "fr-FR"], available) == "de-DE"

    def test_no_match_returns_empty_string(self):
        """Test that no match returns an empty string."""
        available = ["en-US", "fr-FR"]

        assert negotiate_locale(["zh-CN"], available) == ""
        assert negotiate_locale(["es-ES", "it-IT"], available) == ""

    def test_complex_matching_scenarios(self):
        """Test complex real-world matching scenarios."""
        available = ["en", "en-US", "fr", "fr-CA", "es-ES", "zh-CN"]

        # Should prefer specific variant when available
        assert negotiate_locale(["en-US", "en"], available) == "en-US"

        # Should fallback to general when specific not available
        assert negotiate_locale(["en-GB"], available) == "en"

        # Should use alias when exact match not available
        preferred = ["zh"]  # Should alias to zh-CN
        assert negotiate_locale(preferred, available) == "zh-CN"

    def test_mixed_case_and_separator_combinations(self):
        """Test various combinations of case and separator styles."""
        available = ["EN_us", "fr-FR", "De_de"]

        assert negotiate_locale(["en-US"], available) == "EN_us"
        assert negotiate_locale(["FR_fr"], available) == "fr-FR"
        assert negotiate_locale(["de-DE"], available) == "De_de"

    def test_edge_case_malformed_locales(self):
        """Test handling of edge cases and malformed locale strings."""
        available = ["en-US", "fr-FR"]

        # Empty strings in preferred list
        assert negotiate_locale(["", "en-US"], available) == "en-US"

        # Single character locales
        available = ["a", "b-C"]
        assert negotiate_locale(["a"], available) == "a"
        assert negotiate_locale(["b-C"], available) == "b-C"


class TestNegotiateAcceptLanguage:
    """Test cases for the negotiate_accept_language function."""

    def test_full_workflow(self):
        """Test the complete workflow from header to negotiated locale."""
        available = ["en-US", "fr-FR", "es-ES"]

        # Should parse header and negotiate best match
        result = negotiate_accept_language("fr-FR;q=0.9, en-US;q=0.8", available)
        assert result == "fr-FR"

        # Should return default when no match
        result = negotiate_accept_language("zh-CN", available, default="en-US")
        assert result == "en-US"

    def test_empty_header_returns_default(self):
        """Test that empty header returns the default value."""
        available = ["en-US", "fr-FR"]

        result = negotiate_accept_language("", available, default="en-US")
        assert result == "en-US"

        result = negotiate_accept_language(None, available, default="fr-FR")
        assert result == "fr-FR"

    def test_no_match_returns_default(self):
        """Test that no match returns the default value."""
        available = ["en-US", "fr-FR"]

        result = negotiate_accept_language("zh-CN, ja-JP", available, default="en-US")
        assert result == "en-US"

    def test_complex_real_world_scenario(self):
        """Test a complex real-world scenario."""
        available = ["en", "en-US", "fr", "fr-CA", "es", "de"]
        header = "fr-FR;q=0.9, en-GB;q=0.8, en;q=0.7, de;q=0.6"

        # Should match: fr-FR -> fr (fallback), en-GB -> en (fallback), etc.
        result = negotiate_accept_language(header, available)
        assert result == "fr"  # First preference that has a match
