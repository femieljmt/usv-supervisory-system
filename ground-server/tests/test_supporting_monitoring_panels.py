"""Regression tests for placeholder supporting-monitoring panels."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class SupportingMonitoringPanelsTest(TestCase):
    def test_water_quality_and_solar_panels_are_present(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")

        self.assertIn('id="supporting-monitoring"', html)
        self.assertIn("Water Quality Monitoring", html)
        self.assertIn("Solar Panel Monitoring", html)
        self.assertGreaterEqual(html.count("NO DATA"), 4)
        self.assertIn('id="waterPhValue"', html)
        self.assertIn('id="solarPowerValue"', html)

    def test_supporting_panels_have_fixed_height(self) -> None:
        css = (ROOT / "dashboard/static/css/dashboard.css").read_text(encoding="utf-8")

        self.assertIn(".supporting-monitor-panel", css)
        self.assertIn("height: 365px", css)
        self.assertIn("max-height: 365px", css)
        self.assertIn("overflow: hidden", css)

    def test_existing_dashboard_protocol_files_are_not_changed_for_placeholders(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")

        self.assertIn("Belum ada data kualitas air pada session terpilih", html)
        self.assertIn("Belum ada data panel surya pada session terpilih", html)

    def test_supporting_monitoring_charts_are_present(self) -> None:
        html = (ROOT / "dashboard/templates/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "dashboard/static/js/dashboard.js").read_text(encoding="utf-8")

        for chart_id in (
            "waterTempPhChart",
            "waterChemistryChart",
            "solarPvChart",
            "solarBatteryChart",
        ):
            self.assertIn(f'id="{chart_id}"', html)
        self.assertIn("renderSupportingMonitoring", javascript)
        self.assertIn('ctx.fillText("NO DATA"', javascript)
        self.assertIn('ctx.fillText("Waiting for sensor data"', javascript)
        self.assertIn('supporting_sensors', javascript)

    def test_supporting_chart_panels_have_fixed_size(self) -> None:
        css = (ROOT / "dashboard/static/css/dashboard.css").read_text(encoding="utf-8")

        self.assertIn(".sensor-chart-panel", css)
        self.assertIn("height: 340px", css)
        self.assertIn("max-height: 340px", css)
        self.assertIn(".sensor-chart-viewport", css)
        self.assertIn("height: 245px", css)
        self.assertIn("max-height: 245px", css)

