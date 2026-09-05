"""
Zorvane aggregator — Phase 1
Pulls five signals per library, computes fixed-rule risk, writes backend/status.json.
"""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")

GH_HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
GL_HEADERS = {"PRIVATE-TOKEN": GITLAB_TOKEN} if GITLAB_TOKEN else {}

TIMEOUT = 20  # seconds per request

LIBRARIES = {
    "freetype": {
        "name": "FreeType",
        "platform": "github.com",
        "org": "freetype",
        "repo": "freetype",
        "osv_packages": [{"name": "freetype2", "ecosystem": "OSS-Fuzz"}],
        "oc_slug": "freetype",
    },
    "libpng": {
        "name": "libpng",
        "platform": "github.com",
        "org": "pnggroup",
        "repo": "libpng",
        "osv_packages": [{"name": "libpng", "ecosystem": "OSS-Fuzz"}],
        "oc_slug": None,
    },
    "lcms2": {
        "name": "Little CMS",
        "platform": "github.com",
        "org": "mm2",
        "repo": "Little-CMS",
        "osv_packages": [
            {"name": "lcms2", "ecosystem": "OSS-Fuzz"},
            {"name": "lcms2", "ecosystem": "Debian"},
        ],
        "oc_slug": None,
    },
    "ghostscript": {
        "name": "Ghostscript",
        "platform": "github.com",
        "org": "ArtifexSoftware",
        "repo": "ghostpdl",
        "osv_packages": [{"name": "ghostscript", "ecosystem": "OSS-Fuzz"}],
        "oc_slug": None,
    },
    "libjpeg-turbo": {
        "name": "libjpeg-turbo",
        "platform": "github.com",
        "org": "libjpeg-turbo",
        "repo": "libjpeg-turbo",
        "osv_packages": [{"name": "libjpeg-turbo", "ecosystem": "OSS-Fuzz"}],
        "oc_slug": "libjpeg-turbo",
    },
    # libtiff lives on GitLab — bus-factor and Scorecard use gitlab.com paths
    "libtiff": {
        "name": "libtiff",
        "platform": "gitlab.com",
        "org": "libtiff",
        "repo": "libtiff",
        "osv_packages": [{"name": "tiff", "ecosystem": "OSS-Fuzz"}],
        "oc_slug": None,
    },
}

# Bundled-library sets surfaced to consumers (plugins read these from status.json)
KRITA_LIBS = {"libpng", "lcms2", "libjpeg-turbo", "freetype"}
GIMP_LIBS = {"libpng", "lcms2", "libjpeg-turbo", "freetype", "ghostscript", "libtiff"}


# ---------------------------------------------------------------------------
# Signal: OpenSSF Scorecard
# ---------------------------------------------------------------------------

def fetch_scorecard(platform: str, org: str, repo: str) -> Optional[float]:
    url = f"https://api.scorecard.dev/projects/{platform}/{org}/{repo}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json().get("score")
        print(f"  [scorecard] {platform}/{org}/{repo}: HTTP {r.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"  [scorecard] {platform}/{org}/{repo}: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Signal: Bus factor (commit concentration over last 12 months)
# ---------------------------------------------------------------------------

def tier_from_counts(counts: dict) -> Optional[int]:
    """
    Tier 1 = top contributor alone holds >=50% of commits (highest risk)
    Tier 2 = need top 2 contributors to reach 50%
    Tier 3 = need 3–5 contributors to reach 50%
    Tier 4 = need >5 contributors to reach 50% (lowest risk)
    """
    if not counts:
        return None
    total = sum(counts.values())
    if total == 0:
        return None
    sorted_counts = sorted(counts.values(), reverse=True)
    cumulative = 0
    for i, c in enumerate(sorted_counts, start=1):
        cumulative += c
        if cumulative / total >= 0.5:
            if i == 1:
                return 1
            elif i == 2:
                return 2
            elif i <= 5:
                return 3
            else:
                return 4
    return 4


def fetch_bus_factor_github(org: str, repo: str) -> Optional[int]:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    counts: dict = defaultdict(int)
    page = 1
    while True:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{org}/{repo}/commits",
                headers=GH_HEADERS,
                params={"since": since, "per_page": 100, "page": page},
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                print(f"  [busfactor-gh] {org}/{repo} p{page}: HTTP {r.status_code}", file=sys.stderr)
                break
            data = r.json()
            if not data:
                break
            for commit in data:
                login = (commit.get("author") or {}).get("login")
                name = (commit.get("commit", {}).get("author") or {}).get("name", "unknown")
                counts[login or name] += 1
            if len(data) < 100:
                break
            page += 1
            time.sleep(0.25)
        except Exception as e:
            print(f"  [busfactor-gh] {org}/{repo}: {e}", file=sys.stderr)
            break
    return tier_from_counts(counts)


def fetch_bus_factor_gitlab(org: str, repo: str) -> Optional[int]:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    counts: dict = defaultdict(int)
    project_path = f"{org}%2F{repo}"
    page = 1
    while True:
        try:
            r = requests.get(
                f"https://gitlab.com/api/v4/projects/{project_path}/repository/commits",
                headers=GL_HEADERS,
                params={"since": since, "per_page": 100, "page": page},
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                print(f"  [busfactor-gl] {org}/{repo} p{page}: HTTP {r.status_code}", file=sys.stderr)
                break
            data = r.json()
            if not data:
                break
            for commit in data:
                author = commit.get("author_name") or "unknown"
                counts[author] += 1
            if len(data) < 100:
                break
            page += 1
            time.sleep(0.25)
        except Exception as e:
            print(f"  [busfactor-gl] {org}/{repo}: {e}", file=sys.stderr)
            break
    return tier_from_counts(counts)


# ---------------------------------------------------------------------------
# Signal: Open CVEs via OSV.dev
# ---------------------------------------------------------------------------

def fetch_cves(osv_packages: list) -> tuple:
    """Returns (sorted CVE ID list, data_stale bool).
    Only includes CVEs published within the last 3 years and not withdrawn."""
    cve_ids: set = set()
    stale = False
    cutoff = datetime.now(timezone.utc) - timedelta(days=3 * 365)

    for pkg in osv_packages:
        try:
            r = requests.post(
                "https://api.osv.dev/v1/query",
                json={"package": pkg},
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                print(f"  [osv] {pkg}: HTTP {r.status_code}", file=sys.stderr)
                stale = True
                continue
            for vuln in r.json().get("vulns", []):
                if vuln.get("withdrawn"):
                    continue
                published_str = vuln.get("published", "")
                if published_str:
                    try:
                        published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                        if published < cutoff:
                            continue
                    except ValueError:
                        pass
                vid = vuln.get("id", "")
                if vid.startswith("CVE-"):
                    cve_ids.add(vid)
                for alias in vuln.get("aliases", []):
                    if alias.startswith("CVE-"):
                        cve_ids.add(alias)
        except Exception as e:
            print(f"  [osv] {pkg}: {e}", file=sys.stderr)
            stale = True

    return sorted(cve_ids), stale


# ---------------------------------------------------------------------------
# Signal: Funding (GitHub Sponsors + Open Collective)
# ---------------------------------------------------------------------------

def fetch_funding_github_sponsors(org: str) -> Optional[str]:
    query = """
    query($login: String!) {
      repositoryOwner(login: $login) {
        ... on User { sponsorsListing { url } }
        ... on Organization { sponsorsListing { url } }
      }
    }
    """
    try:
        r = requests.post(
            "https://api.github.com/graphql",
            headers={**GH_HEADERS, "Content-Type": "application/json"},
            json={"query": query, "variables": {"login": org}},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            owner = (r.json().get("data") or {}).get("repositoryOwner") or {}
            listing = owner.get("sponsorsListing")
            if listing:
                return listing.get("url") or f"https://github.com/sponsors/{org}"
    except Exception as e:
        print(f"  [funding-gh] {org}: {e}", file=sys.stderr)
    return None


def fetch_funding_opencollective(slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    try:
        r = requests.get(f"https://opencollective.com/{slug}.json", timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("id"):
            return f"https://opencollective.com/{slug}"
    except Exception as e:
        print(f"  [funding-oc] {slug}: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Risk computation — fixed rules for Phase 1
# ---------------------------------------------------------------------------

def compute_risk(
    scorecard: Optional[float],
    bus_factor_tier: Optional[int],
    open_cves: list,
    funding_url: Optional[str],
) -> str:
    has_cves = len(open_cves) > 0
    high_bus_risk = bus_factor_tier is not None and bus_factor_tier <= 2
    low_scorecard = scorecard is not None and scorecard < 5.0

    if has_cves and high_bus_risk:
        return "high"
    if has_cves or (high_bus_risk and funding_url is None) or low_scorecard:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Per-library orchestration
# ---------------------------------------------------------------------------

def process_library(key: str, cfg: dict) -> dict:
    print(f"  {cfg['name']}...", file=sys.stderr)
    platform = cfg["platform"]
    org = cfg["org"]
    repo = cfg["repo"]

    scorecard = fetch_scorecard(platform, org, repo)

    if platform == "github.com":
        bus_factor_tier = fetch_bus_factor_github(org, repo)
    else:
        bus_factor_tier = fetch_bus_factor_gitlab(org, repo)

    open_cves, cve_stale = fetch_cves(cfg["osv_packages"])
    funding_url = fetch_funding_github_sponsors(org) or fetch_funding_opencollective(cfg.get("oc_slug"))
    risk = compute_risk(scorecard, bus_factor_tier, open_cves, funding_url)

    result = {
        "name": cfg["name"],
        "scorecard": scorecard,
        "criticality": None,  # Phase 2: add OSSF Criticality Score
        "bus_factor_tier": bus_factor_tier,
        "open_cves": open_cves,
        "funding_url": funding_url,
        "risk": risk,
    }
    if cve_stale:
        result["data_stale"] = True
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("Zorvane aggregator starting...", file=sys.stderr)

    output = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "krita_libs": sorted(KRITA_LIBS),
        "gimp_libs": sorted(GIMP_LIBS),
        "libraries": {},
    }

    for key, cfg in LIBRARIES.items():
        try:
            output["libraries"][key] = process_library(key, cfg)
        except Exception as e:
            print(f"[ERROR] {key}: {e}", file=sys.stderr)
            output["libraries"][key] = {
                "name": cfg["name"],
                "scorecard": None,
                "criticality": None,
                "bus_factor_tier": None,
                "open_cves": [],
                "funding_url": None,
                "risk": "unknown",
                "data_stale": True,
            }

    out_path = os.path.join(os.path.dirname(__file__), "..", "status.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done. Wrote {os.path.abspath(out_path)}", file=sys.stderr)


if __name__ == "__main__":
    main()
