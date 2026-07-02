from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import ijson


def iter_json_array(file_path: Path) -> Iterator[dict[str, Any]]:
    with file_path.open("rb") as handle:
        try:
            for item in ijson.items(handle, "item"):
                if isinstance(item, dict):
                    yield item
            return
        except ijson.JSONError:
            handle.seek(0)
            payload = json.load(handle)

    if not isinstance(payload, list):
        raise ValueError(f"{file_path.name} must contain a top-level JSON array")

    for item in payload:
        if isinstance(item, dict):
            yield item
