"""Contract tests — verify that _build_cli_args() output is accepted by each
pipeline script's argparse.

These tests reconstruct each script's ArgumentParser (avoiding heavy imports
like aiohttp/aiosqlite) and feed it the exact args that _build_cli_args()
produces. If a contract mismatch exists — wrong flag name, missing required
arg, unrecognized arg — the test fails immediately.

This is the layer that would have caught:
  - --source vs --input mismatch (import pipeline)
  - --staging sent to elevation (doesn't accept it)
  - --bbox value starting with '-' rejected by argparse
"""

import argparse
import os
import sys
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def set_output_dir(tmp_path):
    os.environ["COMPANION_OUTPUT_DIR"] = str(tmp_path)
    yield tmp_path
    del os.environ["COMPANION_OUTPUT_DIR"]


@pytest.fixture
def build_cli_args(set_output_dir):
    import importlib
    import companion
    importlib.reload(companion)
    return companion._build_cli_args


# ---------------------------------------------------------------------------
# Parser factories — mirror each pipeline's argparse exactly
# ---------------------------------------------------------------------------

def _add_common_flags(parser):
    """Add flags common to all pipeline scripts."""
    parser.add_argument("--verbose", "-v", action="store_true", default=False)
    return parser


def _acquire_imagery_parser():
    """Mirror of pipelines/acquire_imagery.py argparse (line 1951-2001)."""
    parser = argparse.ArgumentParser(prog="acquire_imagery.py")
    parser.add_argument("--mode", choices=["tnmaccess", "direct", "m2m", "nationalmap", "noaa"],
                        default="tnmaccess")
    parser.add_argument("--bbox", default="-124.8,31.3,-102.0,49.0")
    parser.add_argument("--output", default="data/imagery.mbtiles")
    parser.add_argument("--zoom", default="0-14")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--staging", default="./staging_imagery")
    parser.add_argument("--concurrency", type=int, default=80)
    parser.add_argument("--m2m-username", default=None)
    parser.add_argument("--m2m-token", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--year", type=int, default=2021)
    return _add_common_flags(parser)


def _download_elevation_parser():
    """Mirror of pipelines/download_elevation.py argparse (line 389-407)."""
    parser = argparse.ArgumentParser(prog="download_elevation.py")
    parser.add_argument("--bbox", default="-124.8,31.3,-102.0,49.0")
    parser.add_argument("--zoom", default="0-12")
    parser.add_argument("--output", default="data/elevation.mbtiles")
    parser.add_argument("--concurrency", type=int, default=10)
    return _add_common_flags(parser)


def _acquire_sentinel_parser():
    """Mirror of pipelines/acquire_sentinel.py parse_args() (line 105-122)."""
    parser = argparse.ArgumentParser(prog="acquire_sentinel.py")
    parser.add_argument("--bbox", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--staging", required=True)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-cloud", type=int, default=20)
    parser.add_argument("--composite", dest="composite", action="store_true", default=True)
    parser.add_argument("--single", dest="composite", action="store_false")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--api-key", default=None)
    return _add_common_flags(parser)


def _import_imagery_parser():
    """Mirror of pipelines/import_imagery.py argparse (line 209-215)."""
    parser = argparse.ArgumentParser(prog="import_imagery.py")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--name", default=None)
    parser.add_argument("--delete-after", action="store_true")
    parser.add_argument("--tileserver-config", default=None)
    _add_common_flags(parser)
    return parser


# ---------------------------------------------------------------------------
# Contract tests — feed _build_cli_args output into the matching parser
# ---------------------------------------------------------------------------

STANDARD_BBOX = "-112.1,33.4,-111.9,33.6"


class TestBasemapContract:
    def test_basemap_with_bbox_parses(self, build_cli_args):
        args = build_cli_args("basemap", {"bbox": STANDARD_BBOX})
        parsed = _acquire_imagery_parser().parse_args(args)
        assert parsed.mode == "tnmaccess"
        assert parsed.bbox == STANDARD_BBOX

    def test_basemap_without_bbox_parses(self, build_cli_args):
        args = build_cli_args("basemap", {})
        parsed = _acquire_imagery_parser().parse_args(args)
        assert parsed.mode == "tnmaccess"
        # Falls back to parser default
        assert parsed.bbox == "-124.8,31.3,-102.0,49.0"


class TestNoaaContract:
    def test_noaa_with_bbox_and_state_parses(self, build_cli_args):
        args = build_cli_args("noaa", {"bbox": STANDARD_BBOX, "state": "AZ"})
        parsed = _acquire_imagery_parser().parse_args(args)
        assert parsed.mode == "noaa"
        assert parsed.bbox == STANDARD_BBOX
        assert parsed.state == "AZ"

    def test_noaa_bbox_only_parses(self, build_cli_args):
        args = build_cli_args("noaa", {"bbox": STANDARD_BBOX})
        parsed = _acquire_imagery_parser().parse_args(args)
        assert parsed.mode == "noaa"
        assert parsed.state is None


class TestM2mContract:
    def test_m2m_full_args_parses(self, build_cli_args):
        args = build_cli_args("m2m", {
            "bbox": STANDARD_BBOX,
            "m2m_username": "user1",
            "m2m_token": "tok123",
        })
        parsed = _acquire_imagery_parser().parse_args(args)
        assert parsed.mode == "m2m"
        assert parsed.bbox == STANDARD_BBOX
        assert parsed.m2m_username == "user1"
        assert parsed.m2m_token == "tok123"

    def test_m2m_without_credentials_parses(self, build_cli_args):
        args = build_cli_args("m2m", {"bbox": STANDARD_BBOX})
        parsed = _acquire_imagery_parser().parse_args(args)
        assert parsed.m2m_username is None
        assert parsed.m2m_token is None


class TestSentinelContract:
    def test_sentinel_full_args_parses(self, build_cli_args):
        args = build_cli_args("sentinel", {
            "bbox": STANDARD_BBOX,
            "api_key": "key123",
        })
        parsed = _acquire_sentinel_parser().parse_args(args)
        assert parsed.bbox == STANDARD_BBOX
        assert parsed.api_key == "key123"

    def test_sentinel_without_bbox_fails(self, build_cli_args):
        """Sentinel requires --bbox. If the frontend doesn't provide one,
        _build_cli_args won't include it, and argparse WILL reject the args.
        This test documents the expected failure."""
        args = build_cli_args("sentinel", {"api_key": "key123"})
        with pytest.raises(SystemExit):
            _acquire_sentinel_parser().parse_args(args)


class TestElevationContract:
    def test_elevation_with_bbox_parses(self, build_cli_args):
        args = build_cli_args("elevation", {"bbox": STANDARD_BBOX})
        parsed = _download_elevation_parser().parse_args(args)
        assert parsed.bbox == STANDARD_BBOX

    def test_elevation_without_bbox_parses(self, build_cli_args):
        args = build_cli_args("elevation", {})
        parsed = _download_elevation_parser().parse_args(args)
        # Falls back to parser default
        assert parsed.bbox == "-124.8,31.3,-102.0,49.0"

    def test_elevation_no_unrecognized_args(self, build_cli_args):
        """Elevation parser must accept ALL args we send — no extras like --staging."""
        args = build_cli_args("elevation", {"bbox": STANDARD_BBOX, "zoom": "0-10"})
        # parse_known_args returns (namespace, extras) — extras must be empty
        _, extras = _download_elevation_parser().parse_known_args(args)
        assert extras == [], f"Unrecognized args sent to elevation: {extras}"


class TestImportContract:
    def test_import_with_source_parses(self, build_cli_args):
        args = build_cli_args("import", {"source": "/path/to/files"})
        parsed = _import_imagery_parser().parse_args(args)
        assert parsed.input == "/path/to/files"

    def test_import_without_source_fails(self, build_cli_args):
        """Import requires --input. Missing source means no --input arg."""
        args = build_cli_args("import", {})
        with pytest.raises(SystemExit):
            _import_imagery_parser().parse_args(args)

    def test_import_no_unrecognized_args(self, build_cli_args):
        args = build_cli_args("import", {"source": "/path/to/files"})
        _, extras = _import_imagery_parser().parse_known_args(args)
        assert extras == [], f"Unrecognized args sent to import: {extras}"


# ---------------------------------------------------------------------------
# Negative-longitude bbox — the specific bug that triggered this test suite
# ---------------------------------------------------------------------------

class TestNegativeLongitudeBbox:
    """The original bug: --bbox -112.1,33.4,-111.9,33.6 caused argparse to
    fail on Windows with 'expected one argument' because the value starting
    with - was misinterpreted. The fix uses --bbox=VALUE syntax."""

    NEGATIVE_BBOXES = [
        "-112.1,33.4,-111.9,33.6",   # Western US (negative west & east)
        "-0.5,51.0,0.5,51.5",         # London (negative west only)
        "-180,-90,180,90",             # Whole world
        "-124.8,31.3,-102.0,49.0",    # Default Western US bbox
    ]

    @pytest.mark.parametrize("bbox", NEGATIVE_BBOXES)
    def test_acquire_imagery_parses_negative_bbox(self, build_cli_args, bbox):
        args = build_cli_args("basemap", {"bbox": bbox})
        parsed = _acquire_imagery_parser().parse_args(args)
        assert parsed.bbox == bbox

    @pytest.mark.parametrize("bbox", NEGATIVE_BBOXES)
    def test_elevation_parses_negative_bbox(self, build_cli_args, bbox):
        args = build_cli_args("elevation", {"bbox": bbox})
        parsed = _download_elevation_parser().parse_args(args)
        assert parsed.bbox == bbox

    @pytest.mark.parametrize("bbox", NEGATIVE_BBOXES)
    def test_sentinel_parses_negative_bbox(self, build_cli_args, bbox):
        args = build_cli_args("sentinel", {"bbox": bbox, "api_key": "k"})
        parsed = _acquire_sentinel_parser().parse_args(args)
        assert parsed.bbox == bbox
