"""Export the diagnostic service contract without starting the application."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi import FastAPI
from app.api.openapi import router

app = FastAPI()
app.include_router(router)
spec = app.openapi()
operations = {"createDiagnosis", "getDiagnosis", "listDiagnoses", "cancelDiagnosis", "resumeDiagnosis"}
spec["paths"] = {path: {method: op for method, op in methods.items() if op.get("operationId") in operations}
                 for path, methods in spec["paths"].items()}
spec["paths"] = {path: methods for path, methods in spec["paths"].items() if methods}
needed = set()


def visit(value):
    if isinstance(value, dict):
        if "$ref" in value:
            name = value["$ref"].split("/")[-1]
            if name not in needed:
                needed.add(name)
                visit(spec["components"]["schemas"][name])
        for child in value.values():
            visit(child)
    elif isinstance(value, list):
        for child in value:
            visit(child)


visit(spec["paths"])
spec["components"]["schemas"] = {name: schema for name, schema in spec["components"]["schemas"].items() if name in needed}
# ALGP's Spring 5.1 client generator consumes OpenAPI 3.0. Preserve nullable
# semantics while translating JSON Schema's null unions to the 3.0 spelling.
def openapi30(value):
    if isinstance(value, list):
        return [openapi30(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = {key: openapi30(item) for key, item in value.items()}
    variants = value.get("anyOf", [])
    nonnull = [item for item in variants if item.get("type") != "null"]
    if variants and len(nonnull) != len(variants):
        del value["anyOf"]
        if len(nonnull) == 1:
            if "$ref" in nonnull[0]:
                value["allOf"] = nonnull
            else:
                value.update(nonnull[0])
        elif nonnull:
            value["anyOf"] = nonnull
        value["nullable"] = True
    if len(value.get("anyOf", [])) > 1:
        # Heterogeneous JSON evidence (object/string/array) and validation paths
        # have no single Java POJO type. Keep the original union as metadata;
        # generators represent the wire value as Object rather than inventing
        # an unusable AnyOf... class. Named request/report fields remain typed.
        value["x-json-schema-anyOf"] = value.pop("anyOf")
    return value


spec = openapi30(spec)
spec["openapi"] = "3.0.3"
for methods in spec["paths"].values():
    for operation in methods.values():
        for media in operation.get("requestBody", {}).get("content", {}).values():
            schema = media.get("schema", {})
            if schema.get("nullable") and len(schema.get("allOf", [])) == 1 and "$ref" in schema["allOf"][0]:
                # Optional request bodies need no wrapper type in the Java SDK.
                media["schema"] = schema["allOf"][0]
spec["servers"] = [{"url": "http://localhost:8000"}]
Path(sys.argv[1]).write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
