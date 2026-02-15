"""Load .env file into environment variables."""
import os
from pathlib import Path


def load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print("WARNING: No .env file found. Copy .env.template to .env and fill in values.")
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                # Expand ~ to home directory (macOS compatibility)
                value = os.path.expanduser(value.strip())
                os.environ[key.strip()] = value
    print(f"Loaded environment from {env_path}")
