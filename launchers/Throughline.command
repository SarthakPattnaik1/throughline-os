#!/usr/bin/env bash
# macOS. Double-click this in Finder and Throughline sets itself up, then starts.
#
# `.command` is the extension Finder runs in Terminal. That is the whole trick,
# and it is also why this file must stay a plain shell script: Finder does not
# read a shebang for the *decision*, only the extension.
#
# Unsigned, so the first double-click raises one Gatekeeper warning — right-click
# and choose Open, once. An invited tester can be told to expect it. The
# `curl | sh` line in the README carries no warning at all, because quarantine is
# set by the downloading browser rather than by the operating system; that is the
# path to hand somebody who has not been spoken to. Signing removes the warning
# for about $100-500 a year and is worth buying the first time this link goes to
# a stranger, not before.
#
# Deliberately NOT `set -e`. A failure here must leave a readable window rather
# than a Terminal that closes on the error the person needed to read.
set -uo pipefail

# Resolve the repository from this file's own location, without a subshell.
# Finder hands a launcher whatever working directory it likes, and `$(...)` forks
# — bash cannot fork from a directory that no longer exists, so the substitution
# dies before the first real command. `${...%/*}` is pure parameter expansion.
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
  echo "  Install it with:  xcode-select --install"
  echo
  read -r -p "Press Return to close this window." _
  exit 1
fi

"$PYTHON" scripts/manage.py start
status=$?

if [ "$status" -ne 0 ]; then
  echo
  echo "Throughline stopped with an error (exit $status)."
  echo "The lines above say why. 'python scripts/manage.py doctor' checks the install."
  read -r -p "Press Return to close this window." _
fi
exit "$status"
