"""允许 `python -m novel_system`。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
