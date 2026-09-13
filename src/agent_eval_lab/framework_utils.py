"""Dependency-free normalization helpers shared by framework adapters."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from typing import Any, Mapping


def get_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def type_name(value: Any) -> str:
    declared = get_field(value, "type")
    return declared if isinstance(declared, str) and declared else type(value).__name__


def jsonable(value: Any) -> Any:
    """Convert common framework/Pydantic objects into report-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if is_dataclass(value):
        return jsonable(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return jsonable(model_dump(mode="json"))
        except TypeError:
            return jsonable(model_dump())
    public = getattr(value, "__dict__", None)
    if isinstance(public, dict):
        return jsonable({key: item for key, item in public.items() if not key.startswith("_")})
    return str(value)


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw": value}
        if isinstance(parsed, dict):
            return jsonable(parsed)
    return {"_raw": jsonable(value)}


def normalize_result(value: Any) -> Any:
    """Prefer a tool artifact, otherwise normalize its content or raw value."""
    artifact = get_field(value, "artifact")
    if artifact is not None:
        return jsonable(artifact)
    content = get_field(value, "content", value)
    if isinstance(content, str):
        try:
            return jsonable(json.loads(content))
        except json.JSONDecodeError:
            return content
    return jsonable(content)


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(value)
