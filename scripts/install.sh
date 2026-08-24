#!/bin/sh
# The front door. Fetches Throughline and sets it up:
#
#     curl -fsSL https://raw.githubusercontent.com/SarthakPattnaik1/throughline-os/main/scripts/install.sh | sh
#
# A terminal line rather than a download, and that is a security decision rather
# than a stylistic one: the quarantine flag that triggers Gatekeeper on macOS and
# SmartScreen on Windows is set by the *downloading browser*, not by the
# operating system, so this path carries no warning at all while an unsigned
# double-clickable wrapper carries one. Homebrew, rustup and uv all ship this way.
# The double-clickable doors are T071, and they wrap this same script.
#
# **This file deliberately does not know which Python to install.** It finds any
# python3 and hands over to scripts/manage.py, which fetches the pinned 3.12
# through scripts/runtimes.py and re-executes itself under it. Duplicating the
# pinned versions and their checksums into shell would create exactly the drift
# this repository has already been bitten by twice — bootstrap.sh installing four
# of nine packages, and the Dockerfile carrying a third copy of the same list.
# One implementation, in the language that can express it.
#
# The chicken-and-egg that remains: a machine with *no* Python at all cannot run
# the thing that fetches Python. Practically every macOS and Linux install has
# some python3; Windows frequently has none, which is why T071's .bat exists.
# That case is reported here rather than papered over.

set -eu

REPO="${THROUGHLINE_REPO:-https://github.com/SarthakPattnaik1/throughline-os.git}"
BRANCH="${THROUGHLINE_BRANCH:-main}"
DEST="${THROUGHLINE_INSTALL_DIR:-$HOME/throughline-os}"

say() { printf '%s\n' "$*"; }
die() { printf '\n%s\n' "$*" >&2; exit 1; }

# Any python3 will do — see the header. 3.8 is the floor for the syntax
# manage.py and runtimes.py actually use, not a preference.
find_python() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)' 2>/dev/null; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

say "Throughline — installing into $DEST"

PYTHON=$(find_python) || die "No Python 3.8+ found on this machine.

  This script needs *some* Python only to start; it then fetches the exact
  version Throughline runs on (3.12) and uses that instead.

    macOS          xcode-select --install
    Debian/Ubuntu  sudo apt install python3
    Fedora/RHEL    sudo dnf install python3

  Then run this again."

say "  using $PYTHON to start"

if [ -d "$DEST/.git" ]; then
  say "  updating the existing checkout"
  git -C "$DEST" fetch --quiet origin "$BRANCH"
  # Never a merge: a local edit that conflicts would stop an install with a
  # message about git, which is not a conversation this script can have.
  git -C "$DEST" checkout --quiet "$BRANCH"
  git -C "$DEST" pull --quiet --ff-only origin "$BRANCH" || die \
    "The checkout at $DEST has diverged from $BRANCH and cannot fast-forward.
  Nothing has been changed. Move it aside, or update it yourself, and re-run."
elif [ -e "$DEST" ]; then
  die "$DEST already exists and is not a git checkout.
  Refusing to write into it. Set THROUGHLINE_INSTALL_DIR to somewhere else."
else
  command -v git >/dev/null 2>&1 || die "git is required to fetch Throughline.

    macOS          xcode-select --install
    Debian/Ubuntu  sudo apt install git
    Fedora/RHEL    sudo dnf install git"
  say "  cloning $REPO"
  git clone --quiet --branch "$BRANCH" "$REPO" "$DEST"
fi

say ""
# From here manage.py owns the sequence: it fetches the pinned CPython if this
# interpreter is the wrong version, re-executes under it, builds the virtualenv,
# installs the workspace, migrates the database and fetches Node if the machine
# has none new enough.
exec "$PYTHON" "$DEST/scripts/manage.py" bootstrap
