import json
import threading
from pathlib import Path

_lock = threading.Lock()

def _load_json(file_path: Path):
    """Load JSON data from *file_path*.
    Returns an empty dict if the file does not exist or is invalid.
    """
    if not file_path.exists():
        return {}
    try:
        with file_path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def _save_json(file_path: Path, data):
    """Write *data* as JSON to *file_path* atomically.
    The directory is created if missing.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix('.tmp')
    with temp_path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_path.replace(file_path)

def read(file_path: str):
    """Thread‑safe read of JSON file.
    Returns a deep copy of the stored dict.
    """
    with _lock:
        return _load_json(Path(file_path))

def write(file_path: str, data) -> None:
    """Thread‑safe write of JSON *data* to *file_path*.
    """
    with _lock:
        _save_json(Path(file_path), data)

def update(file_path: str, updater):
    """Read‑modify‑write helper.
    *updater* receives the current dict and must return the new dict.
    """
    with _lock:
        current = _load_json(Path(file_path))
        new_data = updater(current)
        _save_json(Path(file_path), new_data)
