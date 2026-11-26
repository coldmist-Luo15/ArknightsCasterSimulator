"""Location of the separately prepared V079 runtime data."""
import os
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LEGACY_SNAPSHOT = PROJECT / "runs/2026-11-18-cn2791-gamedata/manifest.json"
DATA_ROOT = Path(os.environ.get("ARKNIGHTS_DATA_DIR", PROJECT if LEGACY_SNAPSHOT.is_file()
                                else PROJECT / ".arkdata")).expanduser().resolve()
