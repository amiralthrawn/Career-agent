"""Create the (git-ignored) private data directory layout.

Usage: python scripts/init_private_dirs.py
"""

from app.core.config import get_settings

SUBDIRECTORIES = ("profile", "documents", "portfolio", "applications", "imports")


def main() -> None:
    root = get_settings().private_data_path
    for name in SUBDIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True)
    print(f"Private data directories ready in {root}")


if __name__ == "__main__":
    main()
