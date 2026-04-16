"""Tests for deploy.py — pure function coverage only, no real SSH needed."""

import pytest

from deploy import (
    source_name_from_filename,
    build_register_command,
    generate_deploy_script,
)


class TestSourceNameFromFilename:
    def test_strips_mbtiles_extension(self):
        assert source_name_from_filename("imagery_noaa.mbtiles") == "imagery_noaa"

    def test_handles_no_extension(self):
        assert source_name_from_filename("foo") == "foo"

    def test_handles_dots_in_name(self):
        assert source_name_from_filename("my.custom.layer.mbtiles") == "my.custom.layer"

    def test_strips_only_mbtiles_not_other_extensions(self):
        # A file named "data.tiff" should keep its extension (only .mbtiles is stripped)
        assert source_name_from_filename("data.tiff") == "data.tiff"

    def test_plain_mbtiles(self):
        assert source_name_from_filename("elevation.mbtiles") == "elevation"

    def test_empty_string(self):
        assert source_name_from_filename("") == ""


class TestBuildRegisterCommand:
    REPO = "/home/pi/geographica"

    def test_uses_container_internal_path(self):
        cmd = build_register_command(self.REPO, "imagery_noaa", "imagery_noaa.mbtiles")
        assert "/srv/data/imagery_noaa.mbtiles" in cmd

    def test_does_not_use_host_path(self):
        cmd = build_register_command(self.REPO, "imagery_noaa", "imagery_noaa.mbtiles")
        assert "/srv/geographica/data/" not in cmd

    def test_includes_tileserver_config_script(self):
        cmd = build_register_command(self.REPO, "imagery_noaa", "imagery_noaa.mbtiles")
        assert "tileserver_config.py" in cmd

    def test_includes_config_json_path(self):
        cmd = build_register_command(self.REPO, "imagery_noaa", "imagery_noaa.mbtiles")
        assert "config.json" in cmd

    def test_includes_add_subcommand(self):
        cmd = build_register_command(self.REPO, "imagery_noaa", "imagery_noaa.mbtiles")
        assert " add " in cmd

    def test_includes_source_name(self):
        cmd = build_register_command(self.REPO, "elevation", "elevation.mbtiles")
        assert "elevation" in cmd

    def test_includes_repo_path(self):
        cmd = build_register_command(self.REPO, "imagery_noaa", "imagery_noaa.mbtiles")
        assert self.REPO in cmd

    def test_uses_python3(self):
        cmd = build_register_command(self.REPO, "imagery_noaa", "imagery_noaa.mbtiles")
        assert "python3" in cmd


class TestGenerateDeployScript:
    REPO = "/home/pi/geographica"
    FILES = ["imagery_noaa.mbtiles", "elevation.mbtiles"]

    def test_includes_set_euo_pipefail(self):
        script = generate_deploy_script(self.FILES, self.REPO)
        assert "set -euo pipefail" in script

    def test_registers_each_file(self):
        script = generate_deploy_script(self.FILES, self.REPO)
        assert "imagery_noaa" in script
        assert "elevation" in script

    def test_uses_container_paths_for_all_files(self):
        script = generate_deploy_script(self.FILES, self.REPO)
        assert "/srv/data/imagery_noaa.mbtiles" in script
        assert "/srv/data/elevation.mbtiles" in script

    def test_does_not_use_host_paths(self):
        script = generate_deploy_script(self.FILES, self.REPO)
        assert "/srv/geographica/data/" not in script

    def test_restarts_tileserver(self):
        script = generate_deploy_script(self.FILES, self.REPO)
        assert "docker compose restart tileserver" in script

    def test_script_is_string(self):
        script = generate_deploy_script(self.FILES, self.REPO)
        assert isinstance(script, str)

    def test_empty_filelist_still_restarts(self):
        script = generate_deploy_script([], self.REPO)
        assert "docker compose restart tileserver" in script
        assert "set -euo pipefail" in script

    def test_includes_repo_path_for_cd(self):
        script = generate_deploy_script(self.FILES, self.REPO)
        assert self.REPO in script
