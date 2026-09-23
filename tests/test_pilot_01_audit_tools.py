from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load("pilot01_verifier_test", "scripts/verify_pilot_01.py")
gate_b = _load("pilot01_gate_b_test", "scripts/run_pilot_01_gate_b.py")


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_checkout_verifier_accepts_exact_clean_head(monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return _Result("a" * 40 + "\n")
        if command[:3] == ["git", "status", "--porcelain=v1"]:
            return _Result("")
        raise AssertionError(command)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    verifier.verify_checkout("a" * 40, True)
    assert len(calls) == 2


def test_checkout_verifier_refuses_wrong_commit(monkeypatch):
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: _Result("b" * 40 + "\n"),
    )
    with pytest.raises(SystemExit, match="audit commit mismatch"):
        verifier.verify_checkout("a" * 40, False)


def test_checkout_verifier_refuses_dirty_tree(monkeypatch):
    def fake_run(command, **_kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return _Result("a" * 40 + "\n")
        if command[:3] == ["git", "status", "--porcelain=v1"]:
            return _Result(" M tests/test_pilot_01_palmer_penguins.py\n")
        raise AssertionError(command)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    with pytest.raises(SystemExit, match="not clean"):
        verifier.verify_checkout("a" * 40, True)


def test_gate_b_record_cannot_be_written_inside_checkout(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_pilot_01_gate_b.py",
            "a" * 40,
            "--operator",
            "independent-auditor",
            "--record",
            str(ROOT / "gate-b.json"),
        ],
    )
    with pytest.raises(SystemExit) as raised:
        gate_b.main()
    assert raised.value.code == 2
