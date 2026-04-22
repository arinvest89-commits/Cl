import yaml
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "settings.yaml"

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

CONFIG = load_config()
