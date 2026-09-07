# Zorvane — Architecture & Design Decisions

## What Is Zorvane

Zorvane monitors the open-source C libraries that Photoshop, GIMP, and Krita all depend on. Those six libraries — FreeType, libpng, Little CMS, Ghostscript, libjpeg-turbo, libtiff — are shared infrastructure for the entire digital-art ecosystem. When one of them has a CVE, a burned-out maintainer, or zero funding, every editor built on top of it is at risk.

The project was motivated by the observation that Adobe's monopoly is partly sustained by the fragility of the open-source layer beneath it. Tools like GIMP and Krita cannot compete if the libraries they depend on silently rot.

---

## Phase Structure

### Phase 1 — Deterministic Aggregator (complete)
Pull five signals per library with no LLM. Compute risk mechanically. Publish a static JSON file and dashboard.

### Phase 2 — LLM Reasoning Loop (pending Nebius API key)
Feed the JSON into a Nebius-hosted model (OpenAI-compatible endpoint) to generate prose recommendations and prioritise action. Will use `NEBIUS_API_KEY` as a GitHub Actions secret.

Photoshop plugin was explicitly skipped — it requires an Adobe developer account and is closed-ecosystem.

---

## Five Signals

| Signal | Source | Why |
|---|---|---|
| OpenSSF Scorecard | api.scorecard.dev | Security hygiene score 0–10 |
| Bus Factor Tier | GitHub/GitLab commit API | Maintainer concentration risk |
| Open CVEs | osv.dev | Known unpatched vulnerabilities |
| GitHub Sponsors | GitHub GraphQL API | Funding signal |
| Open Collective | opencollective.com GraphQL v2 | Funding signal |

---

## Risk Formula

```
high    if CVEs exist AND bus_factor_tier <= 2
medium  if CVEs exist
        OR (bus_factor_tier <= 2 AND no funding)
        OR scorecard < 5.0
low     otherwise
```

Scorecard `null` is treated as unknown (not penalised). Three libraries — FreeType, libpng, Ghostscript — return null because their authoritative repos are not on GitHub (freedesktop.org GitLab, SourceForge, git.ghostscript.com). Their GitHub mirrors are not indexed by Scorecard. This is correct behaviour.

---

## Bus Factor Tiers

| Tier | Meaning | Risk |
|---|---|---|
| 1 | One person owns >50% of commits | Highest |
| 2 | Two people needed to reach 50% | High |
| 3 | Three people needed | Medium |
| 4 | More than three people needed | Low |

Computed from GitHub/GitLab commit history, 52-week window, paginated.

---

## OSV Package Expansion

Early versions queried OSV with only one package name per library and only the OSS-Fuzz ecosystem. This returned zero CVEs for most libraries. Fixed by expanding to 3–4 package name variants per library across OSS-Fuzz, Debian, and Alpine ecosystems.

Example for FreeType:
```python
"osv_packages": [
    {"name": "freetype2", "ecosystem": "OSS-Fuzz"},
    {"name": "freetype",  "ecosystem": "OSS-Fuzz"},
    {"name": "freetype",  "ecosystem": "Debian"},
    {"name": "freetype",  "ecosystem": "Alpine"},
]
```

---

## Open Collective API Decision

The `.json` endpoint (`https://opencollective.com/{slug}.json`) proved unreliable. Switched to the official GraphQL v2 API with a POST request:

```
POST https://api.opencollective.com/graphql/v2
Body: {"query": "query($slug:String!){collective(slug:$slug){id}}", "variables": {"slug": slug}}
```

A 200 response with a non-null `id` confirms the collective exists. This is more robust and the canonical way to query the API.

---

## Library Configuration

All six monitored libraries and their metadata:

| Key | Name | Platform | Org | Repo | OC Slug |
|---|---|---|---|---|---|
| freetype | FreeType | github.com | freetype | freetype | freetype |
| libpng | libpng | github.com | glennrp | libpng | — |
| lcms2 | Little CMS | github.com | mm2 | Little-CMS | — |
| ghostscript | Ghostscript | github.com | ArtifexSoftware | ghostpdl | — |
| libjpeg-turbo | libjpeg-turbo | github.com | libjpeg-turbo | libjpeg-turbo | — |
| libtiff | libtiff | gitlab.com | libtiff | libtiff | — |

libtiff uses GitLab — the aggregator has a separate `fetch_bus_factor_gitlab()` branch for it.

---

## Editor–Library Mapping

Which libraries each editor uses:

| Library | Photoshop | GIMP | Krita |
|---|---|---|---|
| FreeType | ✓ | ✓ | ✓ |
| libpng | ✓ | ✓ | ✓ |
| Little CMS | ✓ | ✓ | ✓ |
| Ghostscript | — | ✓ | — |
| libjpeg-turbo | ✓ | ✓ | ✓ |
| libtiff | ✓ | ✓ | — |

GIMP uses all six. Krita uses four. Photoshop uses five.

KRITA_LIBS in the plugin: `{libpng, lcms2, libjpeg-turbo, freetype}`
GIMP_LIBS in the plugin: `{freetype, ghostscript, lcms2, libjpeg-turbo, libpng, libtiff}`

---

## GitHub Actions Workflows

### daily.yml
- Schedule: `0 6 * * *` (06:00 UTC daily)
- Also supports `workflow_dispatch` for manual runs
- Two jobs: `aggregate` then `deploy`
- `aggregate` runs the Python script and commits `backend/status.json` back to main
- `deploy` publishes `dashboard/` to GitHub Pages
- Push conflict fix: `git pull --rebase origin main` before `git push` — required because manual triggers can race with code pushes

### test.yml
- Runs on every push and every pull request
- Installs requirements + pytest
- Runs both aggregator tests and GIMP plugin tests
- No GIMP runtime required — plugin tests use stdlib only

---

## Dashboard Design

Four tabs: About, Dashboard, Playground, Next Steps.

Dashboard is the default active tab on load.

### Playground Tab
Interactive what-if calculator. User can adjust:
- Scorecard slider (0–10) with N/A checkbox
- Bus factor tier buttons (1–4, tiers 1–2 styled red)
- CVE tags input with `+ Example` button (pre-fills a real or illustrative CVE ID per library)
- Funding toggle (yes/no)

`computeRiskJS()` mirrors the Python formula exactly. `applyToDashboard()` injects the computed values into the main dashboard view with a yellow demo banner to make clear it is simulated data.

### About Tab
Problem statement, four stat pills (300M companies depend on OSS / only 4,200 pay / 60% of maintainers work unpaid / 86% of codebases have known vulnerabilities), library–editor matrix table, and explanation of how Zorvane works.

### Next Steps Tab
Roadmap with status tags: Phase 1 done, Phase 2 LLM loop active, Photoshop plugin soon, token allocation and cross-editor expansion future.

### Example CVE Map
One-click demo CVEs for the Playground (mix of real and illustrative):
```javascript
const EXAMPLE_CVES = {
    freetype:        'CVE-2025-27363',  // real
    ghostscript:     'CVE-2024-29510',  // real
    libpng:          'CVE-2025-04612',
    lcms2:           'CVE-2024-09187',
    'libjpeg-turbo': 'CVE-2025-03441',
    libtiff:         'CVE-2025-07823',
};
```

Editor pills use colour coding: `.ps` = purple, `.gimp` = amber, `.krita` = teal.

---

## GIMP Plugin Architecture

File: `plugins/gimp/zorvane_monitor.py`

Pure-Python functions that can be tested without a GIMP runtime:
- `fetch_status()` — fetches JSON from GitHub raw URL, returns dict or None
- `get_flagged(data, editor_key="gimp_libs")` — returns list of medium/high risk libraries
- `format_alert(flagged)` — short alert string for the startup dialog
- `format_full_report(data)` — full table of all GIMP libraries with scores

At module level, `_startup_check()` runs during GIMP's query phase (before any image is open). A menu item `Filters → Zorvane → Check Library Health` runs the full report on demand.

Python 2/3 compatible urllib imports are used because the GIMP embedded Python version varies.

---

## Krita Plugin Architecture

File: `plugins/krita/zorvane/__init__.py`

Three classes:
- `FetchThread(QThread)` — fetches status.json off the main thread to avoid UI freeze
- `ZorvaneDock(DockWidget)` — docker panel with a table showing Krita's four libraries
- `ZorvaneExtension(Extension)` — fires at startup, shows `QMessageBox` if any library is medium or high risk

Status JSON URL: `https://raw.githubusercontent.com/adse1823/Zorvane/main/backend/status.json`

---

## Testing Decisions

### URL-Based Mock Dispatch
Early tests used `iter([resp1, resp2, ...])` to sequence mocked HTTP responses. After OSV package expansion (3–4 POST calls per library instead of one), iterator order became fragile and tests broke when call counts changed.

Replaced with URL-routing helpers that inspect the URL to decide which mock to return:

```python
def mock_post(url, **kwargs):
    if "osv.dev" in url:         return osv_resp
    elif "opencollective.com" in url: return oc_resp
    else:                         return sponsors_resp  # GitHub GraphQL
```

This approach is robust to reordering and additional calls.

### No GIMP Runtime in Tests
The GIMP plugin is structured so that `gimpfu` is never imported at module level in test context. `sys.path.insert` points pytest at the plugin directory; the test file imports `zorvane_monitor` directly.

---

## Known Limitations (Phase 1)

- Scorecard returns null for FreeType, libpng, Ghostscript — expected, not a bug
- No CVEs shown in initial run — OSV package name expansion deployed but not yet re-run
- No funding URLs found — these libraries do not use GitHub Sponsors or Open Collective
- Bus factor computed from GitHub mirrors for GitLab-primary repos (libtiff) — data may not match upstream

---

## Status as of 2026-09-05 (first real run)

| Library | Scorecard | Bus Factor Tier | Open CVEs | Funding | Risk |
|---|---|---|---|---|---|
| FreeType | null | 2 | 0 | none | medium |
| libpng | null | 1 | 0 | none | medium |
| Little CMS | 6.8 | 1 | 0 | none | medium |
| Ghostscript | null | 2 | 0 | none | medium |
| libjpeg-turbo | 5.3 | 1 | 0 | none | low |
| libtiff | 6.1 | 2 | 0 | none | low |

All at medium or low. The "high" tier will appear once OSV returns CVE matches after the package name fix is redeployed.
