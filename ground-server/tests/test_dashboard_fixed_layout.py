"""Regression tests for bounded dashboard panels and quiet client disconnects."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class DashboardFixedLayoutTest(TestCase):
    def test_all_dynamic_charts_use_fixed_viewport(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        css = (ROOT / "dashboard/static/css/dashboard.css").read_text(encoding="utf-8")
        javascript = (ROOT / "dashboard/static/js/dashboard.js").read_text(encoding="utf-8")

        self.assertEqual(html.count('class="chart-viewport'), 9)
        self.assertIn("height: 250px", css)
        self.assertIn("max-height: 350px", css)
        self.assertIn('canvas.closest(".chart-viewport")', javascript)
        self.assertNotIn('canvas.getAttribute("height")', javascript)

    def test_growing_tables_scroll_inside_fixed_panels(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        css = (ROOT / "dashboard/static/css/dashboard.css").read_text(encoding="utf-8")

        self.assertGreaterEqual(html.count("bounded-table-panel"), 3)
        self.assertGreaterEqual(html.count("fixed-table-wrap"), 3)
        self.assertIn("height: 390px", css)
        self.assertIn("overflow-y: auto", css)
        self.assertIn("scrollbar-gutter: stable", css)

    def test_broken_pipe_is_handled_without_traceback(self) -> None:
        source = (ROOT / "dashboard/app.py").read_text(encoding="utf-8")
        self.assertIn("except (BrokenPipeError, ConnectionResetError)", source)
        self.assertIn("Dashboard client disconnected", source)
