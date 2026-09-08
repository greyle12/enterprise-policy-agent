"""Apply only this evaluation patch after checking every destination for conflicts."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path: Path) -> str:
    # Project text files are LF; accept equivalent PowerShell/Windows newlines.
    return sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Default is read-only conflict check")
    args = parser.parse_args()
    root = args.repo.resolve()
    git_root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, encoding="utf-8"
    ).strip()).resolve()
    if git_root != root:
        raise SystemExit("--repo must be the Git repository root")
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / "PATCH-MANIFEST.json").read_text(encoding="utf-8"))
    pending = []
    for entry in manifest["files"]:
        source = package / entry["path"]
        destination = root / entry["path"]
        if not source.resolve().is_relative_to(package) or not destination.resolve().is_relative_to(root):
            raise SystemExit("Unsafe package path")
        if digest(source) != entry["after_sha256_lf"]:
            raise SystemExit("Package file checksum mismatch: " + entry["path"])
        current = digest(destination) if destination.is_file() else None
        if current == entry["after_sha256_lf"]:
            continue
        if destination.exists() and not destination.is_file():
            raise SystemExit("Destination is not a regular file: " + entry["path"])
        if current != entry["before_sha256_lf"]:
            raise SystemExit("Conflict; no files changed. Preserve/review your local file: " + entry["path"])
        pending.append((source, destination, current))
    if not args.apply:
        print(f"Checks passed; {len(pending)} files would be installed. Add --apply to apply.")
        return
    backup = Path(tempfile.mkdtemp(prefix="retrieval-evidence-backup-")) if pending else None
    for source, destination, expected in pending:
        current = digest(destination) if destination.is_file() else None
        if current != expected:
            raise SystemExit("Concurrent local edit detected; stopped at: " + str(destination))
        if destination.exists():
            backup_file = backup / destination.relative_to(root)
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    print(f"Installed {len(pending)} files. Backup: {backup}. No commit or push performed.")


if __name__ == "__main__":
    main()
