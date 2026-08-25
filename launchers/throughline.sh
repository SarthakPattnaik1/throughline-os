#!/usr/bin/env bash
# Linux. Run this from a terminal, or double-click it where the file manager is
# set to execute scripts — and see `manage.py desktop-entry` for the version that
# appears in the applications menu, which is what most desktops actually respond
# to. GNOME has not run a double-clicked script by default for years.
#
# Deliberately NOT `set -e`: a failure must leave something readable on screen
# rather than a window that closes on the error the person needed to read.
set -uo pipefail

# Resolve the repository from this file's own location, without a subshell — a
# launcher can hand a script a working directory that no longer exists, and bash
# cannot fork from one, so `$(...)` dies before the first real command.
cd -P -- "${BASH_SOURCE[0]%/*}/.."

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Throughline needs any Python 3.8 or newer to start."
  echo "It fetches the exact version it runs on by itself."
  echo
  echo "  Debian/Ubuntu:  sudo apt install python3"
  echo "  Fedora/RHEL:    sudo dnf install python3"
  echo
  read -r -p "Press Return to close." _ 2>/dev/null || true
  exit 1
fi

"$PYTHON" scripts/manage.py start
status=$?

if [ "$status" -ne 0 ]; then
  echo
  echo "Throughline stopped with an error (exit $status)."
  echo "The lines above say why. 'python scripts/manage.py doctor' checks the install."
  # Only when there is a terminal to pause: launched from the applications menu
  # there is no stdin, and a read would hang a process nobody can see.
  [ -t 0 ] && read -r -p "Press Return to close." _
fi
exit "$status"
