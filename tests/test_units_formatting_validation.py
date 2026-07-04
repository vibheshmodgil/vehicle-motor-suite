"""Tests for units.py conversions, formatting helpers, and parse_float."""

import numpy as np
import pytest

from vmi import units
from vmi.formatting import fmt, fmt_km, fmt_pct, fmt_wh
from vmi.validation import parse_float


class TestUnits:
    def test_kmh_mps_roundtrip(self):
        assert units.kmh_to_mps(36.0) == pytest.approx(10.0)
        assert units.mps_to_kmh(10.0) == pytest.approx(36.0)

    def test_wheel_rpm_matches_inline_formula(self):
        # Inline form used across plot modules:
        #   rpm = speeds_kmh * 60 / (2*pi*r) / 3.6
        r = 0.266
        v_kmh = np.array([10.0, 60.0, 90.0])
        inline = v_kmh * 60 / (2 * np.pi * r) / 3.6
        assert np.allclose(units.kmh_to_wheel_rpm(v_kmh, r), inline)

    def test_wheel_rpm_kmh_roundtrip(self):
        rpm = units.kmh_to_wheel_rpm(60.0, 0.266)
        assert units.wheel_rpm_to_kmh(rpm, 0.266) == pytest.approx(60.0)

    def test_motor_rpm_gearing(self):
        assert units.wheel_rpm_to_motor_rpm(500.0, 8.5) == pytest.approx(4250.0)

    def test_rad_s_rpm_roundtrip(self):
        assert units.rpm_to_rad_s(60.0) == pytest.approx(2 * np.pi)
        assert units.rad_s_to_rpm(units.rpm_to_rad_s(3000.0)) == pytest.approx(3000.0)


class TestFormatting:
    def test_fmt_basic(self):
        assert fmt(42.97891402606307) == "42.98"
        assert fmt(1234.5, "Nm", 1) == "1,234.5 Nm"

    def test_fmt_non_numeric_passthrough(self):
        assert fmt("n/a") == "n/a"
        assert fmt(None) == "None"

    def test_helpers(self):
        assert fmt_wh(1234.56) == "1,234.6 Wh"
        assert fmt_km(87.654) == "87.65 km"
        assert fmt_pct(0.8765) == "87.65%"
        assert fmt_pct("bad") == "bad"


class _FakeEntry:
    """Stands in for a CTkEntry: .get() plus a border_color sink."""

    def __init__(self, text):
        self._text = text
        self.border_color = None

    def get(self):
        return self._text

    def configure(self, **kw):
        self.border_color = kw.get("border_color", self.border_color)


class TestParseFloat:
    def test_valid_number(self):
        errors = []
        assert parse_float(_FakeEntry("3.14"), "X", errors=errors) == 3.14
        assert errors == []

    def test_blank_allowed_returns_default(self):
        errors = []
        assert parse_float(_FakeEntry("  "), "X", allow_blank=True,
                           default=7.0, errors=errors) == 7.0
        assert errors == []

    def test_blank_required_is_an_error(self):
        errors = []
        assert parse_float(_FakeEntry(""), "X", errors=errors) is None
        assert errors == ["X is required."]

    def test_non_numeric_is_an_error(self):
        errors = []
        parse_float(_FakeEntry("abc"), "X", errors=errors)
        assert "must be a number" in errors[0]

    def test_bounds(self):
        errors = []
        assert parse_float(_FakeEntry("-1"), "X", minimum=0, errors=errors) is None
        assert parse_float(_FakeEntry("2"), "X", maximum=1, errors=errors) is None
        assert len(errors) == 2
