"""Tests for _build_cli_args() — validates the CLI argument contract between
the FastAPI backend and each pipeline script's argparse.

Every pipeline/arg combination is tested to ensure the produced args are
well-formed and match what the receiving script expects.
"""

import os
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def set_output_dir(tmp_path):
    os.environ["COMPANION_OUTPUT_DIR"] = str(tmp_path)
    yield tmp_path
    del os.environ["COMPANION_OUTPUT_DIR"]


@pytest.fixture
def build_cli_args(set_output_dir):
    """Return the _build_cli_args function after reloading with correct env."""
    import importlib
    import companion
    importlib.reload(companion)
    return companion._build_cli_args


@pytest.fixture
def output_dir(set_output_dir):
    return set_output_dir


# ---------------------------------------------------------------------------
# Basemap pipeline
# ---------------------------------------------------------------------------

class TestBasemap:
    def test_basemap_with_bbox(self, build_cli_args, output_dir):
        args = _build_with_bbox(build_cli_args, "basemap")
        assert "--mode" in args
        assert args[args.index("--mode") + 1] == "tnmaccess"
        assert any(a.startswith("--bbox=") for a in args)
        assert any(a.startswith("--output=") for a in args)
        assert any(a.startswith("--staging=") for a in args)
        assert any(a.startswith("--zoom=") for a in args)

    def test_basemap_without_bbox(self, build_cli_args, output_dir):
        args = build_cli_args("basemap", {})
        assert not any(a.startswith("--bbox") for a in args)
        assert "--mode" in args

    def test_basemap_custom_zoom(self, build_cli_args, output_dir):
        args = build_cli_args("basemap", {"bbox": "-112,33,-111,34", "zoom": "0-10"})
        zoom_arg = [a for a in args if a.startswith("--zoom=")][0]
        assert zoom_arg == "--zoom=0-10"

    def test_basemap_default_zoom(self, build_cli_args, output_dir):
        args = build_cli_args("basemap", {"bbox": "-112,33,-111,34"})
        zoom_arg = [a for a in args if a.startswith("--zoom=")][0]
        assert zoom_arg == "--zoom=0-14"

    def test_basemap_default_output(self, build_cli_args, output_dir):
        args = build_cli_args("basemap", {})
        output_arg = [a for a in args if a.startswith("--output=")][0]
        assert output_arg == f"--output={output_dir / 'basemap.mbtiles'}"


# ---------------------------------------------------------------------------
# NOAA pipeline
# ---------------------------------------------------------------------------

class TestNoaa:
    def test_noaa_with_bbox_and_state(self, build_cli_args, output_dir):
        args = build_cli_args("noaa", {"bbox": "-112,33,-111,34", "state": "AZ"})
        assert args[args.index("--mode") + 1] == "noaa"
        assert "--bbox=-112,33,-111,34" in args
        assert "--state=AZ" in args

    def test_noaa_with_bbox_only(self, build_cli_args, output_dir):
        args = build_cli_args("noaa", {"bbox": "-112,33,-111,34"})
        assert "--bbox=-112,33,-111,34" in args
        assert not any(a.startswith("--state") for a in args)

    def test_noaa_without_bbox(self, build_cli_args, output_dir):
        args = build_cli_args("noaa", {"state": "AZ"})
        assert not any(a.startswith("--bbox") for a in args)
        assert "--state=AZ" in args


# ---------------------------------------------------------------------------
# M2M pipeline
# ---------------------------------------------------------------------------

class TestM2m:
    def test_m2m_full_args(self, build_cli_args, output_dir):
        args = build_cli_args("m2m", {
            "bbox": "-112,33,-111,34",
            "m2m_username": "user1",
            "m2m_token": "tok123",
        })
        assert args[args.index("--mode") + 1] == "m2m"
        assert "--bbox=-112,33,-111,34" in args
        assert "--m2m-username=user1" in args
        assert "--m2m-token=tok123" in args

    def test_m2m_hyphen_key_variants(self, build_cli_args, output_dir):
        """Frontend may send m2m-username (hyphen) or m2m_username (underscore)."""
        args = build_cli_args("m2m", {
            "bbox": "-112,33,-111,34",
            "m2m-username": "user1",
            "m2m-token": "tok123",
        })
        assert "--m2m-username=user1" in args
        assert "--m2m-token=tok123" in args

    def test_m2m_without_credentials(self, build_cli_args, output_dir):
        args = build_cli_args("m2m", {"bbox": "-112,33,-111,34"})
        assert not any(a.startswith("--m2m-username") for a in args)
        assert not any(a.startswith("--m2m-token") for a in args)


# ---------------------------------------------------------------------------
# Sentinel pipeline
# ---------------------------------------------------------------------------

class TestSentinel:
    def test_sentinel_full_args(self, build_cli_args, output_dir):
        args = build_cli_args("sentinel", {
            "bbox": "-112,33,-111,34",
            "api_key": "key123",
        })
        assert "--bbox=-112,33,-111,34" in args
        assert "--api-key=key123" in args
        assert any(a.startswith("--output=") for a in args)
        assert any(a.startswith("--staging=") for a in args)

    def test_sentinel_hyphen_key_variant(self, build_cli_args, output_dir):
        args = build_cli_args("sentinel", {
            "bbox": "-112,33,-111,34",
            "api-key": "key123",
        })
        assert "--api-key=key123" in args

    def test_sentinel_without_bbox(self, build_cli_args, output_dir):
        """Sentinel argparse requires --bbox, but _build_cli_args won't add it
        if not provided. The subprocess will fail — this tests the builder's
        behavior, not the pipeline's validation."""
        args = build_cli_args("sentinel", {"api_key": "key123"})
        assert not any(a.startswith("--bbox") for a in args)


# ---------------------------------------------------------------------------
# Elevation pipeline
# ---------------------------------------------------------------------------

class TestElevation:
    def test_elevation_with_bbox(self, build_cli_args, output_dir):
        args = build_cli_args("elevation", {"bbox": "-112,33,-111,34"})
        assert "--bbox=-112,33,-111,34" in args
        assert any(a.startswith("--zoom=") for a in args)
        assert any(a.startswith("--output=") for a in args)

    def test_elevation_no_staging(self, build_cli_args, output_dir):
        """download_elevation.py does NOT accept --staging."""
        args = build_cli_args("elevation", {"bbox": "-112,33,-111,34"})
        assert not any(a.startswith("--staging") for a in args)

    def test_elevation_custom_zoom(self, build_cli_args, output_dir):
        args = build_cli_args("elevation", {"bbox": "-112,33,-111,34", "zoom": "0-10"})
        assert "--zoom=0-10" in args

    def test_elevation_default_output(self, build_cli_args, output_dir):
        args = build_cli_args("elevation", {})
        output_arg = [a for a in args if a.startswith("--output=")][0]
        assert output_arg == f"--output={output_dir / 'elevation.mbtiles'}"


# ---------------------------------------------------------------------------
# Import pipeline
# ---------------------------------------------------------------------------

class TestImport:
    def test_import_with_source(self, build_cli_args, output_dir):
        args = build_cli_args("import", {"source": "/path/to/files"})
        assert "--input=/path/to/files" in args
        assert any(a.startswith("--output=") for a in args)

    def test_import_no_staging(self, build_cli_args, output_dir):
        """import_imagery.py does NOT accept --staging."""
        args = build_cli_args("import", {"source": "/path/to/files"})
        assert not any(a.startswith("--staging") for a in args)

    def test_import_without_source(self, build_cli_args, output_dir):
        args = build_cli_args("import", {})
        assert not any(a.startswith("--input") for a in args)


# ---------------------------------------------------------------------------
# Bbox edge cases — the primary bug that motivated these tests
# ---------------------------------------------------------------------------

class TestBboxEdgeCases:
    def test_negative_longitude_uses_equals_syntax(self, build_cli_args, output_dir):
        """Negative longitudes like -112.1 must not be split from --bbox by
        subprocess on Windows. The = syntax keeps them as one arg."""
        args = build_cli_args("basemap", {"bbox": "-112.1,33.4,-111.9,33.6"})
        bbox_arg = [a for a in args if a.startswith("--bbox=")][0]
        assert bbox_arg == "--bbox=-112.1,33.4,-111.9,33.6"

    def test_bbox_whitespace_stripped(self, build_cli_args, output_dir):
        """User may accidentally include spaces in bbox fields."""
        args = build_cli_args("basemap", {"bbox": " -112.1,33.4,-111.9,33.6 "})
        bbox_arg = [a for a in args if a.startswith("--bbox=")][0]
        assert bbox_arg == "--bbox=-112.1,33.4,-111.9,33.6"

    def test_bbox_empty_string_treated_as_missing(self, build_cli_args, output_dir):
        """Empty string bbox from frontend should not produce --bbox=''."""
        args = build_cli_args("basemap", {"bbox": ""})
        assert not any(a.startswith("--bbox") for a in args)

    def test_bbox_whitespace_only_treated_as_missing(self, build_cli_args, output_dir):
        args = build_cli_args("basemap", {"bbox": "   "})
        assert not any(a.startswith("--bbox") for a in args)

    def test_bbox_none_treated_as_missing(self, build_cli_args, output_dir):
        args = build_cli_args("basemap", {"bbox": None})
        assert not any(a.startswith("--bbox") for a in args)

    def test_all_args_use_equals_syntax(self, build_cli_args, output_dir):
        """Every arg (except --mode which has a safe value) should use = syntax
        to prevent Windows subprocess splitting issues."""
        args = build_cli_args("basemap", {"bbox": "-112,33,-111,34"})
        for arg in args:
            if arg.startswith("--") and arg != "--mode":
                assert "=" in arg, f"Arg {arg!r} should use --key=value syntax"


# ---------------------------------------------------------------------------
# Unknown pipeline fallback
# ---------------------------------------------------------------------------

class TestUnknownPipeline:
    def test_unknown_pipeline_uses_key_value_pairs(self, build_cli_args, output_dir):
        args = build_cli_args("custom", {"foo": "bar", "baz": "123"})
        assert "--foo=bar" in args
        assert "--baz=123" in args


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _build_with_bbox(build_cli_args, pipeline_name, bbox="-112.1,33.4,-111.9,33.6"):
    return build_cli_args(pipeline_name, {"bbox": bbox})
