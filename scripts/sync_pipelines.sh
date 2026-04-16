#!/bin/bash
# Pipeline script provenance — documents what was copied from the main Geographica repo
# and what workstation-specific changes were applied.
#
# This is documentation for manual sync, not an automated tool.
# When the main repo's pipeline scripts get bug fixes, review this file
# to determine which fixes apply to the companion fork.
#
# Source repo: geographica/scripts/
# Target dir:  pipelines/
#
# Files copied and adapted:
#   acquire_imagery.py   — removed os.setsid/killpg, nice, /dev/stdout->/vsistdout/,
#                          /secrets path, module globals, signal handlers, tileserver imports.
#                          Added unique state file name.
#   acquire_naip.py      — removed nice, module globals, signal handlers.
#                          Added unique state file name.
#   acquire_sentinel.py  — removed nice, /secrets path, module globals, signal handlers.
#                          Added unique state file name.
#   download_elevation.py — removed module globals, signal handlers.
#                          State file already unique (.elevation-state.json).
#   import_imagery.py    — removed /data default, tileserver imports.
#                          Added unique state file name.
#   pipeline_progress.py — copied as-is (no platform-specific code).
#   build_county_index.py — copied as-is (requires GDAL Python bindings).
#
# Files NOT copied (companion-only):
#   orchestrator.py      — parallel subprocess coordinator (new)
