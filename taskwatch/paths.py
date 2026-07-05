from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "taskwatch"
DB_PATH = DATA_DIR / "taskwatch.db"
TIMER_FILE_PATH = DATA_DIR / "timer.json"
TIMER_STATE_PATH = DATA_DIR / "timer_state.json"
AI_CONFIG_PATH = DATA_DIR / "ai_config.json"

CALCURSE_DIR = Path.home() / ".local" / "share" / "calcurse"
APTS_FILE = CALCURSE_DIR / "apts"

INACTIVE_TIMER_DATA: dict[str, str] = {"text": "", "class": "inactive"}
