"""Fail closed when pytest output is missing, incomplete, or skips unexpectedly."""
import re
import sys
from pathlib import Path

ALLOWED = (
    "no Neo4j configured", "could not import 'cv2'",
    "no local embedding model installed", "openai-whisper is not installed",
    "the digitise pack is not installed here",
    "no pack installed in this environment to check against",
    "Main.dc.html is generated", "tests/test_mutating_routes_require_a_session.py",
    "Blender is not installed",
    "optional HDF5 reader is not installed",
)


def check(text):
    if not re.search(r"\b[1-9]\d* passed\b", text):
        raise ValueError("No completed passing-test summary in pytest output")
    unexpected = [line for line in text.splitlines() if line.startswith("SKIPPED")
                  and not any(reason in line for reason in ALLOWED)]
    if unexpected:
        raise ValueError("Unexpected test skips:\n" + "\n".join(unexpected))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1:
            raise ValueError("Usage: check_test_skips.py pytest.out")
        check(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print("All skips accounted for.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
