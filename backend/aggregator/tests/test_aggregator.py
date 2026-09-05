"""
Unit tests for the Zorvane aggregator.
All network calls are mocked — no real API calls are made.
Run with: pytest backend/aggregator/tests/
"""
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Make aggregator importable from any working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aggregator import (
    tier_from_counts,
    compute_risk,
    fetch_scorecard,
    fetch_cves,
    fetch_funding_opencollective,
    fetch_bus_factor_github,
    process_library,
    LIBRARIES,
)


# ---------------------------------------------------------------------------
# tier_from_counts
# ---------------------------------------------------------------------------

class TestTierFromCounts:
    def test_empty_returns_none(self):
        assert tier_from_counts({}) is None

    def test_zero_total_returns_none(self):
        assert tier_from_counts({"alice": 0}) is None

    def test_single_contributor_is_tier_1(self):
        # One person did everything
        assert tier_from_counts({"alice": 100}) == 1

    def test_dominant_contributor_is_tier_1(self):
        # alice has 60 of 100 commits
        assert tier_from_counts({"alice": 60, "bob": 40}) == 1

    def test_two_contributors_needed_is_tier_2(self):
        # alice 40, bob 20 → need both to reach 60/100 = 60%
        # alice alone = 40% < 50%, alice+bob = 60% >= 50%
        assert tier_from_counts({"alice": 40, "bob": 20, "carol": 20, "dave": 20}) == 2

    def test_three_to_five_contributors_is_tier_3(self):
        # Each of 4 contributors has 25 commits; need 2 to reach 50%
        # Actually: 25/100 = 25%, 50/100 = 50% — that's tier 2 (need top 2)
        # To get tier 3 we need 3-5 contributors to reach 50%
        # e.g. 6 people with [20, 15, 15, 15, 15, 20] → sorted: [20,20,15,15,15,15]
        # 20/100=20%, 40/100=40%, 55/100=55% → need 3, tier 3
        assert tier_from_counts({"a": 20, "b": 20, "c": 15, "d": 15, "e": 15, "f": 15}) == 3

    def test_many_contributors_is_tier_4(self):
        # 12 contributors with equal commits → need 6 to reach 50%
        counts = {f"dev{i}": 10 for i in range(12)}
        assert tier_from_counts(counts) == 4

    def test_exactly_50_percent_single(self):
        # 50 commits each — top contributor alone is exactly 50%
        assert tier_from_counts({"alice": 50, "bob": 50}) == 1


# ---------------------------------------------------------------------------
# compute_risk
# ---------------------------------------------------------------------------

class TestComputeRisk:
    def test_high_when_cves_and_high_bus_risk(self):
        assert compute_risk(7.0, 1, ["CVE-2025-001"], "https://opencollective.com/x") == "high"

    def test_high_when_cves_and_bus_tier_2(self):
        assert compute_risk(7.0, 2, ["CVE-2025-001"], None) == "high"

    def test_medium_when_cves_alone(self):
        assert compute_risk(7.0, 4, ["CVE-2025-001"], "https://opencollective.com/x") == "medium"

    def test_medium_when_high_bus_risk_and_no_funding(self):
        assert compute_risk(7.0, 2, [], None) == "medium"

    def test_medium_when_low_scorecard(self):
        assert compute_risk(4.9, 4, [], "https://opencollective.com/x") == "medium"

    def test_medium_scorecard_exactly_5_is_not_triggered(self):
        # scorecard < 5.0 triggers medium; exactly 5.0 does not
        assert compute_risk(5.0, 4, [], "https://opencollective.com/x") == "low"

    def test_low_when_all_signals_healthy(self):
        assert compute_risk(8.5, 4, [], "https://opencollective.com/x") == "low"

    def test_low_when_scorecard_and_bus_factor_none(self):
        # Missing data should not trigger a false positive
        assert compute_risk(None, None, [], None) == "low"

    def test_medium_overrides_when_bus_risk_and_no_funding_even_without_cves(self):
        assert compute_risk(6.0, 1, [], None) == "medium"


# ---------------------------------------------------------------------------
# fetch_scorecard (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchScorecard:
    def test_returns_score_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"score": 6.8}
        with patch("aggregator.requests.get", return_value=mock_resp):
            assert fetch_scorecard("github.com", "freetype", "freetype") == 6.8

    def test_returns_none_on_404(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("aggregator.requests.get", return_value=mock_resp):
            assert fetch_scorecard("github.com", "ghost", "ghost") is None

    def test_returns_none_on_network_error(self):
        with patch("aggregator.requests.get", side_effect=Exception("timeout")):
            assert fetch_scorecard("github.com", "freetype", "freetype") is None


# ---------------------------------------------------------------------------
# fetch_cves (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchCves:
    def _recent_date(self):
        return (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _old_date(self):
        return (datetime.now(timezone.utc) - timedelta(days=4 * 365)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_extracts_cve_from_id_field(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "vulns": [{"id": "CVE-2025-27363", "published": self._recent_date(), "aliases": []}]
        }
        with patch("aggregator.requests.post", return_value=mock_resp):
            cves, stale = fetch_cves([{"name": "freetype2", "ecosystem": "OSS-Fuzz"}])
        assert "CVE-2025-27363" in cves
        assert stale is False

    def test_extracts_cve_from_aliases(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "vulns": [{"id": "GHSA-xxxx-yyyy-zzzz", "published": self._recent_date(), "aliases": ["CVE-2025-99999"]}]
        }
        with patch("aggregator.requests.post", return_value=mock_resp):
            cves, stale = fetch_cves([{"name": "freetype2", "ecosystem": "OSS-Fuzz"}])
        assert "CVE-2025-99999" in cves

    def test_ignores_withdrawn_vulns(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "vulns": [{"id": "CVE-2025-00001", "published": self._recent_date(), "withdrawn": "2025-01-01T00:00:00Z", "aliases": []}]
        }
        with patch("aggregator.requests.post", return_value=mock_resp):
            cves, stale = fetch_cves([{"name": "freetype2", "ecosystem": "OSS-Fuzz"}])
        assert "CVE-2025-00001" not in cves

    def test_ignores_cves_older_than_3_years(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "vulns": [{"id": "CVE-2020-00001", "published": self._old_date(), "aliases": []}]
        }
        with patch("aggregator.requests.post", return_value=mock_resp):
            cves, stale = fetch_cves([{"name": "freetype2", "ecosystem": "OSS-Fuzz"}])
        assert "CVE-2020-00001" not in cves

    def test_sets_stale_on_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("aggregator.requests.post", return_value=mock_resp):
            cves, stale = fetch_cves([{"name": "freetype2", "ecosystem": "OSS-Fuzz"}])
        assert stale is True
        assert cves == []

    def test_sets_stale_on_network_error(self):
        with patch("aggregator.requests.post", side_effect=Exception("connection refused")):
            cves, stale = fetch_cves([{"name": "freetype2", "ecosystem": "OSS-Fuzz"}])
        assert stale is True

    def test_deduplicates_across_packages(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "vulns": [{"id": "CVE-2025-11111", "published": self._recent_date(), "aliases": []}]
        }
        with patch("aggregator.requests.post", return_value=mock_resp):
            cves, _ = fetch_cves([
                {"name": "pkg1", "ecosystem": "OSS-Fuzz"},
                {"name": "pkg2", "ecosystem": "Debian"},
            ])
        assert cves.count("CVE-2025-11111") == 1


# ---------------------------------------------------------------------------
# fetch_funding_opencollective (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchFundingOpencollective:
    def test_returns_url_when_collective_exists(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": 12345, "slug": "freetype"}
        with patch("aggregator.requests.get", return_value=mock_resp):
            url = fetch_funding_opencollective("freetype")
        assert url == "https://opencollective.com/freetype"

    def test_returns_none_when_slug_is_none(self):
        assert fetch_funding_opencollective(None) is None

    def test_returns_none_on_404(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("aggregator.requests.get", return_value=mock_resp):
            assert fetch_funding_opencollective("nonexistent") is None

    def test_returns_none_on_network_error(self):
        with patch("aggregator.requests.get", side_effect=Exception("timeout")):
            assert fetch_funding_opencollective("freetype") is None


# ---------------------------------------------------------------------------
# fetch_bus_factor_github (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchBusFactorGithub:
    def _commit(self, login, name="Dev"):
        return {
            "author": {"login": login},
            "commit": {"author": {"name": name}},
        }

    def test_dominant_contributor_is_tier_1(self):
        page1 = [self._commit("alice")] * 60 + [self._commit("bob")] * 40

        def side_effect(url, **kwargs):
            page = kwargs.get("params", {}).get("page", 1)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = page1 if page == 1 else []
            return mock_resp

        with patch("aggregator.requests.get", side_effect=side_effect):
            tier = fetch_bus_factor_github("freetype", "freetype")
        assert tier == 1

    def test_returns_none_on_api_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("aggregator.requests.get", return_value=mock_resp):
            assert fetch_bus_factor_github("freetype", "freetype") is None

    def test_returns_none_on_empty_response(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        with patch("aggregator.requests.get", return_value=mock_resp):
            assert fetch_bus_factor_github("freetype", "freetype") is None


# ---------------------------------------------------------------------------
# process_library — integration smoke test (all fetchers mocked)
# ---------------------------------------------------------------------------

class TestProcessLibrary:
    def test_output_matches_schema(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

        scorecard_resp = MagicMock(status_code=200)
        scorecard_resp.json.return_value = {"score": 4.5}

        commits_resp = MagicMock(status_code=200)
        commits_resp.json.return_value = [
            {"author": {"login": "alice"}, "commit": {"author": {"name": "Alice"}}}
        ] * 80 + [
            {"author": {"login": "bob"}, "commit": {"author": {"name": "Bob"}}}
        ] * 20

        commits_empty = MagicMock(status_code=200)
        commits_empty.json.return_value = []

        osv_resp = MagicMock(status_code=200)
        osv_resp.json.return_value = {
            "vulns": [{"id": "CVE-2025-27363", "published": recent, "aliases": []}]
        }

        oc_resp = MagicMock(status_code=404)
        sponsors_resp = MagicMock(status_code=200)
        sponsors_resp.json.return_value = {"data": {"repositoryOwner": {"sponsorsListing": None}}}

        get_responses = iter([scorecard_resp, commits_resp, commits_empty, oc_resp])
        post_responses = iter([osv_resp, sponsors_resp])

        with patch("aggregator.requests.get", side_effect=get_responses), \
             patch("aggregator.requests.post", side_effect=post_responses):
            result = process_library("freetype", LIBRARIES["freetype"])

        assert result["name"] == "FreeType"
        assert isinstance(result["scorecard"], float)
        assert result["bus_factor_tier"] in (1, 2, 3, 4)
        assert isinstance(result["open_cves"], list)
        assert result["risk"] in ("low", "medium", "high", "unknown")
        assert "funding_url" in result
        assert result["criticality"] is None  # Phase 2

    def test_data_stale_flag_set_on_osv_failure(self):
        scorecard_resp = MagicMock(status_code=200)
        scorecard_resp.json.return_value = {"score": 7.0}

        commits_resp = MagicMock(status_code=200)
        commits_resp.json.return_value = []

        osv_resp = MagicMock(status_code=503)
        sponsors_resp = MagicMock(status_code=200)
        sponsors_resp.json.return_value = {"data": {"repositoryOwner": None}}
        oc_resp = MagicMock(status_code=404)

        get_responses = iter([scorecard_resp, commits_resp, oc_resp])
        post_responses = iter([osv_resp, sponsors_resp])

        with patch("aggregator.requests.get", side_effect=get_responses), \
             patch("aggregator.requests.post", side_effect=post_responses):
            result = process_library("freetype", LIBRARIES["freetype"])

        assert result.get("data_stale") is True
