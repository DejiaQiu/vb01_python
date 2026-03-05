from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGETS = sorted(
    p
    for p in ROOT.rglob("*.py")
    if "__pycache__" not in p.parts and ".idea" not in p.parts
)


def _run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


def main() -> int:
    rc = _run([sys.executable, "-m", "py_compile", *[str(p) for p in TARGETS]])
    if rc != 0:
        return rc

    rc = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
