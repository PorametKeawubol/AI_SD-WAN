"""Fetch upstream source and refuse a tag that no longer resolves to the locked commit."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    lock = json.loads((ROOT / "lab/upstream-lock.json").read_text(encoding="utf-8"))
    for component, directory in (
        ("fleximanage", "fleximanage"),
        ("flexiagent_reference", "flexiagent"),
    ):
        source = lock[component]
        destination = ROOT / "vendor" / directory
        if not destination.exists():
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    source["tag"],
                    source["repository"],
                    str(destination),
                ],
                check=True,
            )
        actual = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
        ).strip()
        if actual != source["commit"]:
            raise RuntimeError(
                f"{component} differs from lock; existing checkout was not overwritten"
            )
        print(f"{component}: verified {actual}")


if __name__ == "__main__":
    main()
