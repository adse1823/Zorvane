"""
Tests for the GIMP plugin's pure-Python logic.
No GIMP runtime required — gimpfu is not imported in test context.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import zorvane_monitor as plugin


SAMPLE_DATA = {
    "last_updated": "2026-09-05T06:00:00Z",
    "krita_libs": ["freetype", "lcms2", "libjpeg-turbo", "libpng"],
    "gimp_libs":  ["freetype", "ghostscript", "lcms2", "libjpeg-turbo", "libpng", "libtiff"],
    "libraries": {
        "freetype": {
            "name": "FreeType", "scorecard": None, "bus_factor_tier": 2,
            "open_cves": ["CVE-2025-27363"], "funding_url": None, "risk": "high",
        },
        "libpng": {
            "name": "libpng", "scorecard": None, "bus_factor_tier": 1,
            "open_cves": [], "funding_url": None, "risk": "medium",
        },
        "lcms2": {
            "name": "Little CMS", "scorecard": 6.8, "bus_factor_tier": 1,
            "open_cves": [], "funding_url": None, "risk": "medium",
        },
        "ghostscript": {
            "name": "Ghostscript", "scorecard": None, "bus_factor_tier": 2,
            "open_cves": [], "funding_url": None, "risk": "medium",
        },
        "libjpeg-turbo": {
            "name": "libjpeg-turbo", "scorecard": 5.3, "bus_factor_tier": 1,
            "open_cves": [], "funding_url": None, "risk": "low",
        },
        "libtiff": {
            "name": "libtiff", "scorecard": 6.1, "bus_factor_tier": 2,
            "open_cves": [], "funding_url": None, "risk": "low",
        },
    },
}


class TestGetFlagged:
    def test_returns_medium_and_high(self):
        flagged = plugin.get_flagged(SAMPLE_DATA)
        names = [f["name"] for f in flagged]
        assert "FreeType" in names
        assert "libpng" in names
        assert "Little CMS" in names
        assert "Ghostscript" in names

    def test_excludes_low_risk(self):
        flagged = plugin.get_flagged(SAMPLE_DATA)
        names = [f["name"] for f in flagged]
        assert "libjpeg-turbo" not in names
        assert "libtiff" not in names

    def test_only_includes_gimp_libs(self):
        # libjpeg-turbo is in gimp_libs but low risk — should not appear
        flagged = plugin.get_flagged(SAMPLE_DATA, editor_key="gimp_libs")
        for f in flagged:
            assert f["risk"] in ("medium", "high")

    def test_empty_when_all_healthy(self):
        healthy_data = {
            "gimp_libs": ["libpng"],
            "libraries": {
                "libpng": {"name": "libpng", "risk": "low", "open_cves": [], "funding_url": None},
            },
        }
        assert plugin.get_flagged(healthy_data) == []

    def test_empty_when_no_gimp_libs(self):
        data = {"gimp_libs": [], "libraries": SAMPLE_DATA["libraries"]}
        assert plugin.get_flagged(data) == []


class TestFormatAlert:
    def test_contains_library_names(self):
        flagged = plugin.get_flagged(SAMPLE_DATA)
        msg = plugin.format_alert(flagged)
        assert "FreeType" in msg
        assert "libpng" in msg

    def test_contains_risk_level(self):
        flagged = [{"name": "FreeType", "risk": "high", "open_cves": ["CVE-2025-27363"]}]
        msg = plugin.format_alert(flagged)
        assert "HIGH" in msg

    def test_contains_cve_ids(self):
        flagged = [{"name": "FreeType", "risk": "high", "open_cves": ["CVE-2025-27363"]}]
        msg = plugin.format_alert(flagged)
        assert "CVE-2025-27363" in msg

    def test_no_cves_shows_no_cve_string(self):
        flagged = [{"name": "libpng", "risk": "medium", "open_cves": []}]
        msg = plugin.format_alert(flagged)
        assert "CVE" not in msg

    def test_header_present(self):
        msg = plugin.format_alert([])
        assert "Zorvane" in msg


class TestFormatFullReport:
    def test_contains_all_gimp_libs(self):
        report = plugin.format_full_report(SAMPLE_DATA)
        assert "FreeType" in report
        assert "Ghostscript" in report
        assert "libtiff" in report

    def test_contains_scorecard_score(self):
        report = plugin.format_full_report(SAMPLE_DATA)
        assert "6.8" in report   # lcms2 scorecard score

    def test_contains_last_updated(self):
        report = plugin.format_full_report(SAMPLE_DATA)
        assert "2026-09-05" in report

    def test_null_scorecard_shows_na(self):
        report = plugin.format_full_report(SAMPLE_DATA)
        assert "n/a" in report


class TestFetchStatus:
    def test_returns_none_on_network_error(self):
        from unittest.mock import patch
        with patch("zorvane_monitor.urlopen", side_effect=Exception("offline")):
            assert plugin.fetch_status() is None

    def test_returns_parsed_dict_on_success(self):
        import json
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(SAMPLE_DATA).encode("utf-8")
        with patch("zorvane_monitor.urlopen", return_value=mock_resp):
            data = plugin.fetch_status()
        assert data["last_updated"] == "2026-09-05T06:00:00Z"

    def test_returns_none_on_invalid_json(self):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        with patch("zorvane_monitor.urlopen", return_value=mock_resp):
            assert plugin.fetch_status() is None
