"""Exercise the actual shell snippet in an isolated directory (never on an instance)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from server.provisioner import _generate_env_overrides, _write_env_script


def run_merge(
    directory: Path, overrides: dict[str, str], *, existing_required: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-e", "-c", _write_env_script(overrides, existing_required=existing_required)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )


def read_values(directory: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (directory / ".env").read_text().splitlines()
        if line and not line.startswith("#")
    )


def test_new_site_generates_unique_secret_and_applies_overrides(tmp_path: Path):
    (tmp_path / ".env.example").write_text("SECRET_KEY=placeholder\nDEFAULT=from_upstream\nCUSTOM=old\n")
    result = run_merge(tmp_path, {"CUSTOM": "from_ui", "TOKEN": "a=b#c"}, existing_required=False)
    assert result.returncode == 0, result.stderr
    initial = read_values(tmp_path)
    assert initial["SECRET_KEY"] != "placeholder"
    assert len(initial["SECRET_KEY"]) >= 32
    assert initial["CUSTOM"] == "from_ui"
    assert initial["TOKEN"] == "a=b#c"
    assert os.stat(tmp_path / ".env").st_mode & 0o777 == 0o600

    # Existing values survive an update; newly added upstream defaults appear.
    (tmp_path / ".env.example").write_text("SECRET_KEY=another_placeholder\nDEFAULT=changed\nNEW=added\n")
    result = run_merge(tmp_path, {"CUSTOM": "from_ui", "TOKEN": "a=b#c"})
    assert result.returncode == 0, result.stderr
    updated = read_values(tmp_path)
    assert updated["SECRET_KEY"] == initial["SECRET_KEY"]
    assert updated["TOKEN"] == "a=b#c"
    assert updated["DEFAULT"] == "from_upstream"
    assert updated["NEW"] == "added"
    assert updated["CUSTOM"] == "from_ui"


def test_existing_instance_retains_key_and_manual_values(tmp_path: Path):
    (tmp_path / ".env.example").write_text("SECRET_KEY=example\nKEEP=default\nNEW=upstream\n")
    (tmp_path / ".env").write_text("# local changes\nSECRET_KEY='original-key'\nKEEP=manual\nEXTRA=manual-only\n")
    result = run_merge(tmp_path, {"UI_OVERRIDE": "saved"})
    assert result.returncode == 0, result.stderr
    assert read_values(tmp_path) == {
        "SECRET_KEY": "'original-key'", "KEEP": "manual", "NEW": "upstream",
        "EXTRA": "manual-only", "UI_OVERRIDE": "saved",
    }
    assert "original-key" not in result.stdout + result.stderr


def test_flare_overrides_take_precedence_on_redeploy(tmp_path: Path):
    (tmp_path / ".env.example").write_text("SECRET_KEY=example\nAPI_KEY=default\n")
    (tmp_path / ".env").write_text("SECRET_KEY=stable\nAPI_KEY=previous\nMANUAL=keep\n")
    result = run_merge(tmp_path, {"API_KEY": "ui-updated"})
    assert result.returncode == 0, result.stderr
    assert read_values(tmp_path) == {"SECRET_KEY": "stable", "API_KEY": "ui-updated", "MANUAL": "keep"}


def test_removed_ui_override_reverts_to_default_and_keeps_unmanaged_values(tmp_path: Path):
    (tmp_path / ".env.example").write_text("SECRET_KEY=example\nFROM_UI=upstream-default\n")
    (tmp_path / ".env").write_text("SECRET_KEY=stable\nMANUAL=untouched\n")
    assert run_merge(tmp_path, {"FROM_UI": "custom", "OTHER_UI": "temporary"}).returncode == 0
    assert run_merge(tmp_path, {}).returncode == 0
    assert read_values(tmp_path) == {
        "SECRET_KEY": "stable", "FROM_UI": "upstream-default", "MANUAL": "untouched",
    }


def test_existing_missing_or_invalid_env_fails_without_writing(tmp_path: Path):
    (tmp_path / ".env.example").write_text("SECRET_KEY=example\n")
    assert run_merge(tmp_path, {}).returncode != 0
    assert not (tmp_path / ".env").exists()
    for contents in ("BROKEN LINE\nSECRET_KEY=stable\n", "VALUE=hi\n", "SECRET_KEY=\n"):
        (tmp_path / ".env").write_text(contents)
        assert run_merge(tmp_path, {}).returncode != 0
        assert (tmp_path / ".env").read_text() == contents


def test_invalid_overrides_and_symlink_do_not_replace_existing_env(tmp_path: Path):
    (tmp_path / ".env.example").write_text("SECRET_KEY=example\n")
    original = "SECRET_KEY=stable\n"
    (tmp_path / ".env").write_text(original)
    for overrides in ({"MULTILINE": "bad\nvalue"}, {"SECRET_KEY": "new-key"}, {"INVALID KEY": "value"}):
        assert run_merge(tmp_path, overrides).returncode != 0
        assert (tmp_path / ".env").read_text() == original
    (tmp_path / ".env").unlink()
    (tmp_path / "source").write_text(original)
    (tmp_path / ".env").symlink_to("source")
    assert run_merge(tmp_path, {}).returncode != 0
    assert (tmp_path / ".env").is_symlink()


def test_bad_upstream_defaults_leave_existing_env_untouched(tmp_path: Path):
    original = "SECRET_KEY=stable\nMANUAL=preserved\n"
    (tmp_path / ".env").write_text(original)
    (tmp_path / ".env.example").write_text("NOT AN ENV ASSIGNMENT\n")
    assert run_merge(tmp_path, {"CUSTOM": "safe"}).returncode != 0
    assert (tmp_path / ".env").read_text() == original


def test_override_values_cannot_become_shell_commands(tmp_path: Path):
    (tmp_path / ".env.example").write_text("SECRET_KEY=placeholder\n")
    payload = "test' ; touch unexpected-file ; echo 'other"
    result = run_merge(tmp_path, {"CUSTOM": payload}, existing_required=False)
    assert result.returncode == 0
    assert read_values(tmp_path)["CUSTOM"] == payload
    assert not (tmp_path / "unexpected-file").exists()
    assert payload not in result.stdout + result.stderr


def test_flare_generated_overrides_do_not_rotate_secret():
    site = SimpleNamespace(env_overrides={"CUSTOM": "saved"})
    assert _generate_env_overrides(site) == _generate_env_overrides(site)
    assert "SECRET_KEY" not in _generate_env_overrides(site)
    assert _generate_env_overrides(site)["CUSTOM"] == "saved"
