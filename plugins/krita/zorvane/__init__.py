"""
Zorvane Krita plugin
On startup, fetches status.json and alerts if any Krita-bundled library is
medium or high risk. Also registers a docker panel for on-demand viewing.

Installation:
  Copy zorvane/ and zorvane.desktop into Krita's pykrita folder:
    Linux:   ~/.local/share/krita/pykrita/
    Windows: %APPDATA%\krita\pykrita\
    macOS:   ~/Library/Application Support/krita/pykrita/
  Then enable the plugin in Krita → Settings → Configure Krita → Python Plugins.

Configuration:
  Set STATUS_JSON_URL below to the raw URL of your published status.json.
"""

import json
import urllib.request
import urllib.error
from typing import Optional

from krita import Extension, DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QSizePolicy,
)

# ---------------------------------------------------------------------------
# Configuration — update this after pushing the repo to GitHub
# ---------------------------------------------------------------------------
STATUS_JSON_URL = (
    "https://raw.githubusercontent.com/adse1823/Zorvane/main/backend/status.json"
)

RISK_COLOURS = {
    "high":    "#c0392b",
    "medium":  "#e67e22",
    "low":     "#27ae60",
    "unknown": "#7f8c8d",
}

DOCKER_ID = "zorvane_docker"


# ---------------------------------------------------------------------------
# Background fetch thread
# ---------------------------------------------------------------------------

class FetchThread(QThread):
    """Fetches status.json off the main thread to avoid blocking Krita's UI."""
    finished = pyqtSignal(object)  # emits parsed dict or None on failure

    def run(self):
        try:
            with urllib.request.urlopen(STATUS_JSON_URL, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            self.finished.emit(data)
        except Exception:
            self.finished.emit(None)


# ---------------------------------------------------------------------------
# Docker panel
# ---------------------------------------------------------------------------

class ZorvaneDock(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Zorvane Library Monitor")
        self._data: Optional[dict] = None

        root = QWidget()
        self._layout = QVBoxLayout(root)
        self._layout.setContentsMargins(8, 8, 8, 8)

        self._status_label = QLabel("Fetching library data…")
        self._status_label.setWordWrap(True)
        self._layout.addWidget(self._status_label)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Library", "Risk", "CVEs", "Funding"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._layout.addWidget(self._table)

        self.setWidget(root)

    def canvasChanged(self, canvas):
        pass  # required by Krita's dock API

    def populate(self, data: dict):
        self._data = data
        libs = data.get("libraries", {})
        krita_keys = set(data.get("krita_libs", []))

        self._table.setRowCount(0)
        updated = data.get("last_updated", "unknown")
        self._status_label.setText(f"Last updated: {updated}")

        for key, info in libs.items():
            row = self._table.rowCount()
            self._table.insertRow(row)

            name_item = QTableWidgetItem(info.get("name", key))
            if key in krita_keys:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
            self._table.setItem(row, 0, name_item)

            risk = info.get("risk", "unknown")
            risk_item = QTableWidgetItem(risk.upper())
            risk_item.setForeground(Qt.white)
            risk_item.setBackground(Qt.GlobalColor(0))  # reset, set via stylesheet below
            risk_item.setData(Qt.UserRole, risk)
            self._table.setItem(row, 1, risk_item)

            cves = info.get("open_cves", [])
            self._table.setItem(row, 2, QTableWidgetItem(", ".join(cves) if cves else "—"))

            funding = info.get("funding_url") or "—"
            self._table.setItem(row, 3, QTableWidgetItem(funding))

        self._table.resizeRowsToContents()


# ---------------------------------------------------------------------------
# Extension (startup hook)
# ---------------------------------------------------------------------------

class ZorvaneExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self._thread: Optional[FetchThread] = None

    def setup(self):
        self._thread = FetchThread()
        self._thread.finished.connect(self._on_fetch_done)
        self._thread.start()

    def _on_fetch_done(self, data: Optional[dict]):
        if data is None:
            # Fail silently — no alert if status.json is unreachable
            return

        krita_keys = set(data.get("krita_libs", []))
        libs = data.get("libraries", {})

        flagged = [
            info for key, info in libs.items()
            if key in krita_keys and info.get("risk") in ("medium", "high")
        ]

        if flagged:
            self._show_alert(flagged)

        # Push data into the docker panel if it's already open
        dock = self._find_dock()
        if dock:
            dock.populate(data)

    def _show_alert(self, flagged: list):
        lines = []
        for info in flagged:
            name = info.get("name", "?")
            risk = info.get("risk", "?").upper()
            cves = info.get("open_cves", [])
            cve_str = f" — {', '.join(cves)}" if cves else ""
            lines.append(f"• {name}  [{risk}]{cve_str}")

        msg = QMessageBox()
        msg.setWindowTitle("Zorvane: Library Risk Alert")
        msg.setIcon(QMessageBox.Warning)
        msg.setText(
            "One or more open-source libraries bundled by Krita have flagged risks:"
        )
        msg.setInformativeText("\n".join(lines))
        msg.setDetailedText(
            "These libraries are tracked by Zorvane for security vulnerabilities, "
            "maintainer bus-factor, and funding health.\n\n"
            f"Source: {STATUS_JSON_URL}\n\n"
            "Open the 'Zorvane Library Monitor' docker for full details."
        )
        msg.exec_()

    def _find_dock(self) -> Optional[ZorvaneDock]:
        app = Krita.instance()
        for window in app.windows():
            for dock in window.dockers():
                if dock.objectName() == DOCKER_ID:
                    return dock
        return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def registerExtension(app):
    app.addExtension(ZorvaneExtension(app))


Krita.instance().addExtension(ZorvaneExtension(Krita.instance()))
Krita.instance().addDockWidgetFactory(
    DockWidgetFactory(DOCKER_ID, DockWidgetFactoryBase.DockRight, ZorvaneDock)
)
