"""Entry used by launchers: `uv run python run.py serve ...`."""
import sys

from vjhstudio.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
