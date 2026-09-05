# Zorvane — Design Document

Status: Draft, ready for implementation
Audience: an autonomous building agent implementing this from scratch, plus the project owner
Companion doc: see `README.md` for motivation/background — this document is the actionable spec

## 1. Summary

Zorvane tracks the health, security, and funding status of six open-source libraries shared by GIMP, Krita, and (per Adobe's own third-party notices) Photoshop. It publishes a single machine-readable status file, and surfaces alerts directly inside the creative apps that bundle these libraries via native plugins, plus a public dashboard.

Two phases:
- **Phase 1 (build now):** a scheduled script computes risk from public data using fixed rules. No LLM involved.
- **Phase 2 (build later, out of scope for this pass):** replace the fixed-rule risk computation with an LLM reasoning/action loop (perceive → reason → act → log) that can draft GitHub issues, funding appeals, and eventually trigger a payments/token allocation. Do not build phase 2 yet — design the phase-1 output so it can be swapped later without touching the plugins or dashboard.

## 2. Goals / Non-goals

**Goals**
- Track 6 named libraries for security, maintainer, and funding risk using free public data sources.
- Publish one canonical status file that every other component reads.
- Ship a working alert inside Krita (primary target) on app startup.
- Ship a public static dashboard reading the same file.

**Non-goals for this pass**
- No LLM calls, no agent reasoning, no autonomous actions (phase 2).
- No payment/token execution (future stretch).
- No GIMP or Photoshop plugin required to ship first — build Krita first, treat the others as follow-on work using the same pattern.
- No user accounts, no login, no database — everything is a static file plus scheduled compute.

## 3. Tracked libraries

| Key | Library | Role | Known repo (verify at implementation time — host may be GitHub or GitLab) |
|---|---|---|---|
| `freetype` | FreeType | Font rendering | github.com/freetype/freetype |
| `libpng` | libpng | PNG codec | github.com/pnggroup/libpng |
| `lcms2` | Little CMS | Color management | github.com/mm2/Little-CMS |
| `ghostscript` | Ghostscript | PostScript/PDF interpreter | github.com/ArtifexSoftware/ghostpdl |
| `libjpeg-turbo` | libjpeg-turbo | JPEG codec | github.com/libjpeg-turbo/libjpeg-turbo |
| `libtiff` | libtiff | TIFF codec | **confirm host — as of last check this project is hosted on GitLab (gitlab.com/libtiff/libtiff), not GitHub; the bus-factor and Scorecard data sources below assume GitHub and will need a GitLab-compatible path for this one library** |

The building agent should verify each repo URL still resolves and is the canonical upstream before wiring data pulls against it — do not hardcode without a live check, library repos do move.

## 4. Architecture

```
[Data sources]                [Engine: scheduled job]        [Output]                    [Consumers]
OpenSSF Scorecard API   \                                    
OpenSSF Criticality      >--> aggregator script  ------->    status.json  ---->  Krita plugin (startup alert)
OSV.dev (CVEs)                (runs daily via cron)          (hosted publicly)   GIMP plugin (later)
GitHub API (bus-factor) /                                                        Photoshop plugin (later)
GitHub Sponsors /                                                                Dashboard (static site)
Open Collective (funding)
```

Every consumer is a thin client of `status.json`. The aggregator is the only component that talks to the five data sources. This separation is deliberate: phase 2 replaces only the aggregator's internals (fixed rules → LLM reasoning) without touching any consumer.

## 5. Component: the aggregator (build first — everything else depends on it)

**Responsibility:** for each of the 6 libraries, pull five signals and compute one risk level, then write `status.json`.

**Data pulls per library:**
1. **Scorecard score** — OpenSSF Scorecard REST API (`api.scorecard.dev` or run `scorecard` CLI against the repo) → float 0–10
2. **Criticality score** — `ossf/criticality_score` tool output or its published dataset → float 0–1
3. **Bus factor** — via GitHub REST/GraphQL API: pull commit authorship over the last 12 months, compute the % of commits made by the top 1–2 contributors. Store as an integer risk tier: `1` = one contributor >50% of commits (very high risk) … `4` = more than 5 contributors needed to reach 50% (low risk). Same method as OpenSauced's "Lottery Factor," reimplemented directly against the GitHub API rather than depending on their service.
4. **Open CVEs** — query OSV.dev's API (`api.osv.dev/v1/query`) by package name/ecosystem, collect currently-unresolved CVE IDs
5. **Funding status** — check GitHub Sponsors (via GitHub API, `sponsorsListing` on the org/user) and Open Collective's public API for an active funding page; record the URL if found, `null` if not

**Risk computation (fixed rule for phase 1 — replace in phase 2):**
```
risk = "high"   if open_cves is non-empty AND bus_factor_tier <= 2
risk = "medium" if open_cves is non-empty
              OR (bus_factor_tier <= 2 AND funding_url is null)
              OR scorecard < 5.0
risk = "low"    otherwise
```

**Output schema (`status.json`):**
```json
{
  "last_updated": "2026-09-05T06:00:00Z",
  "libraries": {
    "freetype": {
      "name": "FreeType",
      "scorecard": 4.2,
      "criticality": 0.87,
      "bus_factor_tier": 1,
      "open_cves": ["CVE-2025-27363"],
      "funding_url": "https://opencollective.com/freetype",
      "risk": "high"
    }
  }
}
```

**Runtime:** a script (language: agent's choice — Python is the natural fit given the available data-source client libraries) run on a schedule via GitHub Actions cron (e.g. daily at 06:00 UTC). No server. Output committed to the repo or pushed to GitHub Pages so it's fetchable over plain HTTPS by every consumer.

**Acceptance criteria:**
- Running the script produces a valid `status.json` matching the schema above for all 6 libraries.
- Re-running it without any upstream data change produces byte-identical risk levels (deterministic).
- A deliberately-broken data source (e.g. OSV.dev unreachable) fails that one library's CVE field gracefully (empty list + a `"data_stale": true` flag) rather than crashing the whole run.

## 6. Component: Krita plugin (build second)

**Responsibility:** alert the user on Krita startup if any Krita-bundled library (a subset of the 6 — confirm which ones Krita actually links against; likely `libpng`, `lcms2`) is `medium` or `high` risk.

**Implementation:** Python `Extension` subclass (Krita's `libkis` API). Register a `setup()` method — Krita calls this automatically once on startup. Inside it:
1. HTTP GET the published `status.json`
2. Filter to Krita's bundled library keys
3. If any are flagged, show a Krita notification/message box
4. Also register a docker (persistent side panel) showing full status for all flagged libraries, viewable anytime after the startup alert is dismissed

**Acceptance criteria:**
- Installing the plugin in Krita and launching it triggers a check within a few seconds of startup.
- If `status.json` is unreachable (offline), the plugin fails silently — no error dialog, no crash, just no alert.
- The docker panel opens from Krita's dockers menu and shows current data even if no risk was flagged at startup.

## 7. Component: dashboard (build third — trivial once #5 exists)

**Responsibility:** public static page rendering `status.json` as a human-readable table — library name, risk level, open CVEs, funding link.

**Implementation:** plain HTML/CSS/JS, no framework required. Fetches `status.json` client-side or is regenerated alongside it. Hosted on GitHub Pages.

**Acceptance criteria:** loads with no build step, correctly reflects the current `status.json`, funding links are clickable and correct.

## 8. Component: GIMP plugin (build fourth)

Same responsibility and data contract as the Krita plugin. Implementation differs: GIMP's Python-Fu plugins are normally menu-triggered; there is no first-class "run on startup" hook like Krita's `Extension.setup()`. Workaround: GIMP executes each installed plugin script once during its startup "query" phase (to read `register()` metadata) — place the status check in that code path so it runs once per GIMP launch. Also register a normal menu item for on-demand viewing.

**Acceptance criteria:** same as Krita's, adapted to GIMP's message API (`gimp.message`) instead of a docker panel (a simple dialog is sufficient for v1).

## 9. Component: Photoshop plugin (build last — most external friction)

UXP plugin (JS/HTML/CSS). Manifest declares a persistent panel and network permission. On panel load, fetch `status.json`, compare against the libraries Adobe's own Third-Party Notices document confirms are bundled (currently confirmed: FreeType — verify others before shipping, do not assume libpng/lcms2/etc. are bundled in Photoshop without a source). Because Photoshop is closed-source, this plugin cannot verify the actual linked version at runtime — the alert text must say "Photoshop bundles FreeType (per Adobe's published notices)" rather than implying a live version check.

**Distribution note:** requires an Adobe developer account and Adobe Exchange review before end users can install it. Treat this as a separate workstream from building the plugin itself.

## 10. Repo structure

```
Zorvane/
  README.md              # background/motivation (existing)
  DESIGN.md              # this document
  backend/
    aggregator/           # the scheduled script + its data-source clients
    status.json            # generated output (or published via Pages from here)
  plugins/
    krita/
    gimp/
    photoshop/
  dashboard/
  .github/workflows/       # the cron job definition
```

## 11. Build order

1. `backend/aggregator` — the engine, including the risk rule and `status.json` output. Nothing else can be tested without this.
2. `.github/workflows` cron wiring — confirm the aggregator actually runs unattended on a schedule and publishes its output somewhere fetchable over HTTPS.
3. `plugins/krita` — first consumer, proves the end-to-end loop (data → plugin → user-visible alert).
4. `dashboard` — trivial once the JSON is live.
5. `plugins/gimp`
6. `plugins/photoshop`
7. **Stop here for this pass.** Phase 2 (agentic reasoning layer) and the payments/token stretch goal are explicitly out of scope until phase 1 is working end-to-end.

## 12. Open questions for the building agent to resolve during implementation

- Confirm libtiff's current canonical repo host (GitHub vs GitLab) and adjust the bus-factor/Scorecard data pull accordingly for that one library.
- Confirm exactly which of the 6 libraries Krita and GIMP each actually link against at build time (don't assume — check each project's build dependencies) so plugin alerts don't flag libraries that aren't actually present.
- Confirm which of the 6 libraries beyond FreeType are actually named in Adobe's Photoshop Third-Party Notices before including them in the Photoshop plugin's alert set.
- Decide exact hosting for `status.json` (GitHub Pages vs. committing to the repo directly) based on how the cron job is wired.
