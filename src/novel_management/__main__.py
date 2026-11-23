"""允许 `python -m novel_management`。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
