"""Scan tracked/staged publication content without printing matched secrets."""

import re
import subprocess
from pathlib import Path

PATTERNS = [
    rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    rb"AKIA[0-9A-Z]{16}",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    b"/" + rb"home/[^/\s]+/|/" + rb"Users/[^/\s]+/|[A-Z]:\\Users\\",
]


def main():
    paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    failures = []
    for raw in paths:
        if not raw:
            continue
        path = Path(raw.decode())
        if path.name == ".env" or any(
            part in {".venv", ".runtime", "node_modules"} for part in path.parts
        ):
            failures.append(f"forbidden runtime path: {path}")
            continue
        data = path.read_bytes()
        if path.suffix in {".db", ".sqlite", ".pem", ".key", ".log", ".deb"}:
            failures.append(f"forbidden artifact type: {path}")
        if path.suffix not in {".png", ".jpg", ".woff2"} and any(
            re.search(pattern, data) for pattern in PATTERNS
        ):
            failures.append(f"sensitive pattern requires review: {path}")
    for failure in failures:
        print(failure)
    if failures:
        raise SystemExit(1)
    print(f"Public-content scan passed for {len(paths) - 1} tracked/staged files.")


if __name__ == "__main__":
    main()
