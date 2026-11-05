from typing import Any

from pydantic import BaseModel


_SCHEMA_KEYS_TO_DROP = frozenset({
    "additionalProperties",
    "additional_properties",
    "$defs",
    "definitions",
    "$schema",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "multipleOf",
    "pattern",
    "title",
})
_FORMATS_TO_DROP = frozenset({"date-time", "uuid", "uri", "email"})


def build_gemini_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic schema into the subset accepted by Gemini JSON mode."""
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.get("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                definition = defs.get(ref[len("#/$defs/"):])
                if definition is not None:
                    return inline(dict(definition))
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(value) for value in node]
        return node

    def simplify(node: Any) -> Any:
        if isinstance(node, list):
            return [simplify(value) for value in node]
        if not isinstance(node, dict):
            return node

        result = {
            key: simplify(value)
            for key, value in node.items()
            if key not in _SCHEMA_KEYS_TO_DROP
            and not (key == "format" and value in _FORMATS_TO_DROP)
        }
        any_of = result.get("anyOf")
        if isinstance(any_of, list):
            non_null = [value for value in any_of if value != {"type": "null"}]
            if len(non_null) == 1 and isinstance(non_null[0], dict):
                result.pop("anyOf")
                result.update(non_null[0])
            elif any(
                isinstance(value, dict) and value.get("type") == "number"
                for value in any_of
            ) and any(
                isinstance(value, dict) and value.get("type") == "string"
                for value in any_of
            ):
                result.pop("anyOf")
                result["type"] = "number"
        return result

    resolved = inline(raw)
    if isinstance(resolved, dict):
        resolved.pop("$defs", None)
    return simplify(resolved)