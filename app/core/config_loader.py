import json
from pathlib import Path


def load_base_config() -> dict:
    path = Path("db") / "base_config.json"
    return json.loads(path.read_text(encoding="utf-8"))