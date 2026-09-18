import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA = Path(os.environ.get("JEV_DATA_DIR", ROOT / "data"))
os.environ.setdefault("BU_NAME", "jev-service")
os.environ.setdefault("BU_CDP_URL", "http://127.0.0.1:9276")
os.environ.setdefault("BH_HOME", str(DATA / "harness"))


def missing_keys():
    return [k for k in ("TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY") if not os.environ.get(k)]
