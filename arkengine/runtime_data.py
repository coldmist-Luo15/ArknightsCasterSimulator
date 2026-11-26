"""Location of the separately prepared V079 runtime data."""
import os
from pathlib import Path


DATA_ROOT = Path(os.environ.get("ARKNIGHTS_DATA_DIR", Path(__file__).resolve().parents[1])).expanduser().resolve()
