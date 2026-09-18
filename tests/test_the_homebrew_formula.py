"""`brew install` for the Mac, generated from the signed manifest (T194).

The formula can only ever point at an archive the release key signed, with the
checksum the manifest states, at the name the release builder writes. These use
the published v0.3.0 manifest as a fixture — public, and signed — so nothing
here reaches the network.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "release" / "manifest-v0.3.0.json"
HOST = "https://throughline-research.pages.dev/latest.json"

spec = importlib.util.spec_from_file_location("homebrew", ROOT / "scripts" / "homebrew.py")
homebrew = importlib.util.module_from_spec(spec)
spec.loader.exec_module(homebrew)


def manifest() -> dict:
    return json.loads(FIXTURE.read_text())


def test_a_signed_manifest_becomes_a_pinned_formula():
    version, url, digest = homebrew.archive_url(homebrew.verified(manifest()), HOST)
    text = homebrew.render(version, url, digest)
    assert f'url "https://throughline-research.pages.dev/throughline-{version}.tar.gz"' in text
    assert f'sha256 "{manifest()["sha256"]}"' in text
    assert 'license "Apache-2.0"' in text


def test_a_manifest_the_release_key_did_not_sign_is_refused():
    tampered = copy.deepcopy(manifest())
    tampered["sha256"] = "0" * 64
    with pytest.raises(homebrew.FormulaError, match="did not verify"):
        homebrew.verified(tampered)


@pytest.mark.parametrize("name", ["../throughline-v0.3.0.tar.gz", "latest.tar.gz",
                                  "throughline-v0.2.0.tar.gz", "/etc/passwd"])
def test_the_archive_is_only_ever_this_versions_immutable_name(name):
    changed = dict(manifest(), file=name)
    with pytest.raises(homebrew.FormulaError, match="archive name"):
        homebrew.archive_url(changed, HOST)


def test_an_archive_not_served_over_https_is_refused():
    with pytest.raises(homebrew.FormulaError, match="HTTPS"):
        homebrew.archive_url(manifest(), "http://throughline-research.pages.dev/latest.json")


def test_the_committed_formula_is_the_generated_one():
    """Nobody hand-edits it: the file on disk is exactly what the manifest says."""
    version, url, digest = homebrew.archive_url(homebrew.verified(manifest()), HOST)
    assert homebrew.FORMULA.read_text() == homebrew.render(version, url, digest)


def test_the_formula_adds_a_door_not_a_second_install():
    """It installs the release's own launcher and nothing else, and says where
    the data is — so `brew uninstall` is never mistaken for deleting research."""
    text = homebrew.FORMULA.read_text()
    installs = re.findall(r'libexec\.install "([^"]+)"', text)
    assert installs == ["scripts/install.sh"]
    assert "~/.throughline-os" in text and "never touches your data" in text


@pytest.mark.skipif(not shutil.which("ruby"), reason="no ruby to parse the formula")
def test_the_formula_is_valid_ruby():
    done = subprocess.run(["ruby", "-c", str(homebrew.FORMULA)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
