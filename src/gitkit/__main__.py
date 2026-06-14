"""Entry point: python -m gitkit <repo-path>"""
import sys

from gitkit.ui.app import run


def main() -> int:
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    run(repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
