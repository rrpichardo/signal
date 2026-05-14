from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(config_path: str) -> None:
    """Load a .env file into os.environ if keys aren't already set.

    Searches CWD first, then the directory containing the config file.
    Skips variables that are already in the environment so an explicit
    export always wins over the file.
    """
    candidates = [
        Path.cwd() / ".env",
        Path(config_path).parent.parent / ".env",  # configs/../.env
    ]
    for dotenv in candidates:
        if not dotenv.is_file():
            continue
        for raw in dotenv.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            line = line.removeprefix("export").strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        return  # stop after first .env found
