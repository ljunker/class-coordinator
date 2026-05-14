from pathlib import Path
import os


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "programs"
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
DB_PATH = Path(os.environ.get("CLASS_COORDINATOR_DB", ROOT / "class_coordinator.sqlite3"))
TINYAUTH_LOGOUT_URL = os.environ.get("TINYAUTH_LOGOUT_URL", "").strip()
