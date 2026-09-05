#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zorvane GIMP plugin
Checks open-source library health on GIMP startup and via a menu item.

Installation (GIMP 2.10):
  Copy this file into GIMP's plug-ins folder:
    Linux:   ~/.config/GIMP/2.10/plug-ins/
    Windows: %APPDATA%\GIMP\2.10\plug-ins\
    macOS:   ~/Library/Application Support/GIMP/2.10/plug-ins/
  Make it executable on Linux/macOS: chmod +x zorvane_monitor.py
  Restart GIMP — the check runs automatically and a menu item appears
  under Filters > Zorvane > Check Library Health.

Configuration:
  Update STATUS_JSON_URL below to match your published status.json URL.
"""

import json
import sys

# Python 2/3 urllib compatibility (GIMP 2.10 ships Python 2 on some platforms)
try:
    from urllib.request import urlopen
    from urllib.error import URLError
except ImportError:
    from urllib2 import urlopen, URLError  # type: ignore

try:
    from gimpfu import register, main, gimp, PF_IMAGE, PF_DRAWABLE, PROCEDURE_PLUGIN, RUN_NONINTERACTIVE
    _IN_GIMP = True
except ImportError:
    # Allow importing outside GIMP for testing
    _IN_GIMP = False

STATUS_JSON_URL = (
    "https://raw.githubusercontent.com/adse1823/Zorvane/main/backend/status.json"
)

TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Core logic (testable without GIMP)
# ---------------------------------------------------------------------------

def fetch_status():
    """Fetch and parse status.json. Returns dict or None on failure."""
    try:
        resp = urlopen(STATUS_JSON_URL, timeout=TIMEOUT)
        return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_flagged(data, editor_key="gimp_libs"):
    """Return list of library info dicts that are medium or high risk for the given editor."""
    editor_keys = set(data.get(editor_key, []))
    libs = data.get("libraries", {})
    return [
        info for key, info in libs.items()
        if key in editor_keys and info.get("risk") in ("medium", "high")
    ]


def format_alert(flagged):
    """Format a plain-text alert message for flagged libraries."""
    lines = ["Zorvane Library Risk Alert", ""]
    for info in flagged:
        name = info.get("name", "?")
        risk = info.get("risk", "?").upper()
        cves = info.get("open_cves", [])
        cve_str = " — " + ", ".join(cves) if cves else ""
        lines.append(u"• {} [{}]{}".format(name, risk, cve_str))
    lines.append("")
    lines.append("Open Filters > Zorvane > Check Library Health for details.")
    return "\n".join(lines)


def format_full_report(data):
    """Format a full status report for on-demand display."""
    libs = data.get("libraries", {})
    gimp_keys = set(data.get("gimp_libs", []))
    updated = data.get("last_updated", "unknown")
    lines = [u"Zorvane Library Status  (updated: {})".format(updated), ""]

    for key, info in libs.items():
        if key not in gimp_keys:
            continue
        name  = info.get("name", key)
        risk  = info.get("risk", "unknown").upper()
        score = info.get("scorecard")
        tier  = info.get("bus_factor_tier")
        cves  = info.get("open_cves", [])
        fund  = info.get("funding_url") or "None"

        score_str = "{:.1f}/10".format(score) if score is not None else "n/a"
        tier_str  = str(tier) if tier is not None else "n/a"
        cves_str  = ", ".join(cves) if cves else "None"

        lines.append(u"{} [{}]".format(name, risk))
        lines.append(u"  Scorecard: {}  Bus-factor tier: {}".format(score_str, tier_str))
        lines.append(u"  CVEs: {}".format(cves_str))
        lines.append(u"  Funding: {}".format(fund))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GIMP plugin functions
# ---------------------------------------------------------------------------

def _startup_check():
    """
    Runs once during GIMP's query phase (startup). Shows an alert if any
    GIMP-bundled library is medium or high risk. Fails silently if offline.
    """
    data = fetch_status()
    if data is None:
        return
    flagged = get_flagged(data)
    if flagged and _IN_GIMP:
        gimp.message(format_alert(flagged))


def run_check(image, drawable):
    """Menu-triggered on-demand check — shows full report."""
    data = fetch_status()
    if data is None:
        if _IN_GIMP:
            gimp.message("Zorvane: Could not reach status.json.\nCheck your internet connection.")
        return
    flagged = get_flagged(data)
    report  = format_full_report(data)
    if _IN_GIMP:
        if flagged:
            gimp.message(format_alert(flagged) + "\n\n" + report)
        else:
            gimp.message("Zorvane: All monitored GIMP libraries are healthy.\n\n" + report)


# ---------------------------------------------------------------------------
# Startup check (runs during GIMP's query/registration phase)
# ---------------------------------------------------------------------------

if _IN_GIMP:
    try:
        _startup_check()
    except Exception:
        pass  # never crash GIMP startup


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

if _IN_GIMP:
    register(
        "python-fu-zorvane-check",
        "Check open-source library health for GIMP",
        "Fetches Zorvane status.json and shows risk alerts for GIMP-bundled libraries.",
        "Zorvane",
        "Zorvane",
        "2026",
        "<Image>/Filters/Zorvane/Check Library Health",
        "*",
        [],
        [],
        run_check,
    )
    main()
