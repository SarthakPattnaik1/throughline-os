"""Regression guards for local-first network exposure and backup privacy."""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_compose_publishes_local_services_only_on_loopback():
    compose = (ROOT / "compose.yaml").read_text()

    # Docker binds HOST:CONTAINER mappings to 0.0.0.0 when no host address is
    # supplied. These services are local-first/admin surfaces, so their default
    # Compose path must never make them LAN-visible merely by being started.
    for port in (8080, 7474, 7687, 11434):
        assert f'"127.0.0.1:{port}:{port}"' in compose
        assert not re.search(rf'(?m)^\s*-\s*"{port}:{port}"', compose), (
            f"compose.yaml exposes port {port} on every host interface")


def test_documented_docker_run_is_loopback_only():
    readme = (ROOT / "README.md").read_text()
    assert "127.0.0.1:8080:8080" in readme


def test_backup_forces_private_permissions():
    backup = (ROOT / "scripts" / "backup.sh").read_text()

    # Research archives must not inherit a permissive shell umask on a shared
    # workstation even after the usable hosted-model credential is excluded.
    assert re.search(r"(?m)^umask 077\s*$", backup)
    assert 'chmod 600 "$ARCHIVE"' in backup


def test_backup_excludes_saved_model_credentials():
    backup = (ROOT / "scripts" / "backup.sh").read_text()
    assert "--exclude-table-data=installation_secrets" in backup, (
        "backup.sh would copy usable installation credentials into portable "
        "backup archives")
