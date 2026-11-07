"""Упрощённое чтение описания API (OpenAPI 3.x и Swagger 2.0 в формате JSON).

Нужен один вопрос: какие поля обязательны в успешном ответе на GET по заданному пути.
Читаются только локальные ссылки `$ref`; внешние адреса не загружаются."""

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

Spec = dict[str, Any]

MAX_REF_DEPTH = 10
MAX_ARRAY_ITEMS = 20


@dataclass(frozen=True)
class ResponseShape:
    """Форма успешного ответа: объект или массив объектов и их обязательные поля."""

    is_array: bool
    required: tuple[str, ...]


def load_spec(body: bytes) -> Spec | None:
    """Разбирает описание API. None, если это не JSON или не OpenAPI 3.x / Swagger 2.0."""
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("openapi", "")).startswith("3.") or data.get("swagger") == "2.0":
        return data
    return None


def _resolve(spec: Spec, node: Any) -> dict[str, Any] | None:
    """Идёт по цепочке локальных `$ref`. Циклы, внешние ссылки и обрыв дают None."""
    for _ in range(MAX_REF_DEPTH):
        if not isinstance(node, dict):
            return None
        ref = node.get("$ref")
        if ref is None:
            return node
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return None  # внешние ссылки не загружаем
        target: Any = spec
        for part in ref[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                return None
            target = target[part]
        node = target
    return None


def _required(spec: Spec, schema: Any, depth: int = 0) -> list[str]:
    """Обязательные поля схемы, включая объединённые через allOf."""
    resolved = _resolve(spec, schema)
    if resolved is None or depth > MAX_REF_DEPTH:
        return []
    names: list[str] = [n for n in resolved.get("required", []) if isinstance(n, str)]
    for part in resolved.get("allOf", []):
        names.extend(_required(spec, part, depth + 1))
    return list(dict.fromkeys(names))


def _path_candidates(spec: Spec, path: str) -> list[str]:
    """Путь как есть и без префикса сервера (servers[].url или basePath)."""
    prefixes: list[str] = []
    base_path = spec.get("basePath")
    if isinstance(base_path, str):
        prefixes.append(base_path.rstrip("/"))
    for server in spec.get("servers", []):
        if isinstance(server, dict) and isinstance(server.get("url"), str):
            prefixes.append(urlsplit(server["url"]).path.rstrip("/"))
    candidates = [path]
    for prefix in prefixes:
        if prefix and path.startswith(prefix + "/"):
            candidates.append(path[len(prefix) :])
    return candidates


def _find_operation(spec: Spec, path: str) -> dict[str, Any] | None:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return None
    for candidate in _path_candidates(spec, path):
        if isinstance(paths.get(candidate), dict):
            return _get_operation(paths[candidate])
        for template, item in paths.items():
            if "{" in template and isinstance(item, dict) and _matches(template, candidate):
                return _get_operation(item)
    return None


def _matches(template: str, path: str) -> bool:
    pattern = "".join(
        "[^/]+" if part.startswith("{") else re.escape(part)
        for part in re.split(r"(\{[^}]*\})", template)
        if part
    )
    return re.fullmatch(pattern, path) is not None


def _get_operation(item: dict[str, Any]) -> dict[str, Any] | None:
    operation = item.get("get")
    return operation if isinstance(operation, dict) else None


def _response_schema(operation: dict[str, Any]) -> Any:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return None
    response = responses.get("200")
    if not isinstance(response, dict):
        return None
    if "schema" in response:  # Swagger 2.0
        return response["schema"]
    content = response.get("content")
    if isinstance(content, dict):
        for media_type, body in content.items():
            if "json" in media_type and isinstance(body, dict):
                return body.get("schema")
    return None


def response_shape(spec: Spec, path: str) -> ResponseShape | None:
    """Форма ответа 200 на GET по пути. None, если проверять нечего."""
    operation = _find_operation(spec, path)
    if operation is None:
        return None
    schema = _resolve(spec, _response_schema(operation))
    if schema is None:
        return None
    is_array = schema.get("type") == "array" or "items" in schema
    target = schema.get("items") if is_array else schema
    required = _required(spec, target)
    return ResponseShape(is_array, tuple(required)) if required else None


def check_required(data: Any, shape: ResponseShape) -> list[str] | None:
    """Чего не хватает в ответе. [] если всё на месте, None если проверять нечего."""
    if shape.is_array:
        if not isinstance(data, list):
            return ["ожидался массив"]
        items = [item for item in data[:MAX_ARRAY_ITEMS] if isinstance(item, dict)]
        if not items:
            return None
        problems = []
        for name in shape.required:
            absent = sum(1 for item in items if name not in item)
            if absent:
                problems.append(f"{name} (в {absent} из {len(items)} элементов)")
        return problems
    if not isinstance(data, dict):
        return ["ожидался объект"]
    return [name for name in shape.required if name not in data]
