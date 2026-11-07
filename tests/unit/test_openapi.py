import json

from app.core.openapi import ResponseShape, check_required, load_spec, response_shape


def spec_bytes(data):
    return json.dumps(data).encode("utf-8")


PET = {
    "type": "object",
    "required": ["name", "photoUrls"],
    "properties": {"name": {"type": "string"}, "photoUrls": {"type": "array"}},
}


def v3(paths, schemas=None, servers=None):
    spec = {"openapi": "3.0.3", "paths": paths, "components": {"schemas": schemas or {}}}
    if servers:
        spec["servers"] = servers
    return spec


def get_200(schema):
    return {"get": {"responses": {"200": {"content": {"application/json": {"schema": schema}}}}}}


# load_spec


def test_load_spec_accepts_openapi_3_and_swagger_2():
    assert load_spec(spec_bytes({"openapi": "3.0.3", "paths": {}})) is not None
    assert load_spec(spec_bytes({"swagger": "2.0", "paths": {}})) is not None


def test_load_spec_rejects_non_json_and_foreign_json():
    assert load_spec(b"openapi: 3.0.3\npaths: {}") is None  # YAML в MVP не читаем
    assert load_spec(b'{"hello": "world"}') is None
    assert load_spec(b"[1, 2]") is None
    assert load_spec(spec_bytes({"openapi": "2.5"})) is None


# response_shape


def test_object_response_with_required_fields():
    spec = v3({"/pet": get_200(PET)})
    assert response_shape(spec, "/pet") == ResponseShape(False, ("name", "photoUrls"))


def test_ref_to_components_is_resolved():
    spec = v3({"/pet": get_200({"$ref": "#/components/schemas/Pet"})}, {"Pet": PET})
    assert response_shape(spec, "/pet") == ResponseShape(False, ("name", "photoUrls"))


def test_array_response_uses_items_required_fields():
    schema = {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}}
    spec = v3({"/pets": get_200(schema)}, {"Pet": PET})
    assert response_shape(spec, "/pets") == ResponseShape(True, ("name", "photoUrls"))


def test_all_of_required_fields_are_merged():
    schema = {"allOf": [{"required": ["id"]}, {"required": ["name", "id"]}]}
    spec = v3({"/x": get_200(schema)})
    assert response_shape(spec, "/x") == ResponseShape(False, ("id", "name"))


def test_swagger_2_definitions_and_direct_schema():
    spec = {
        "swagger": "2.0",
        "paths": {
            "/pet": {"get": {"responses": {"200": {"schema": {"$ref": "#/definitions/Pet"}}}}}
        },
        "definitions": {"Pet": PET},
    }
    assert response_shape(spec, "/pet") == ResponseShape(False, ("name", "photoUrls"))


def test_path_template_matches_but_exact_path_wins():
    other = {"type": "object", "required": ["exact"]}
    spec = v3({"/pet/{id}": get_200(PET), "/pet/me": get_200(other)})
    assert response_shape(spec, "/pet/42") == ResponseShape(False, ("name", "photoUrls"))
    assert response_shape(spec, "/pet/me") == ResponseShape(False, ("exact",))


def test_server_path_prefix_is_removed_when_user_path_contains_it():
    spec = v3({"/pet/{id}": get_200(PET)}, servers=[{"url": "https://x.example/api/v3"}])
    assert response_shape(spec, "/api/v3/pet/1") == ResponseShape(False, ("name", "photoUrls"))


def test_swagger_2_base_path_prefix_is_removed():
    spec = {
        "swagger": "2.0",
        "basePath": "/v2",
        "paths": {"/pet": {"get": {"responses": {"200": {"schema": PET}}}}},
    }
    assert response_shape(spec, "/v2/pet") == ResponseShape(False, ("name", "photoUrls"))


def test_returns_none_when_nothing_to_check():
    no_required = {"type": "object", "properties": {"a": {}}}
    spec = v3(
        {
            "/a": get_200(no_required),
            "/b": {"post": {"responses": {}}},
            "/c": {"get": {"responses": {"404": {}}}},
        }
    )
    for path in ["/a", "/b", "/c", "/missing"]:
        assert response_shape(spec, path) is None


def test_circular_and_external_refs_do_not_hang_or_crash():
    schemas = {"A": {"$ref": "#/components/schemas/B"}, "B": {"$ref": "#/components/schemas/A"}}
    spec = v3(
        {
            "/loop": get_200({"$ref": "#/components/schemas/A"}),
            "/ext": get_200({"$ref": "https://evil.example.org/schema.json"}),
        },
        schemas,
    )
    assert response_shape(spec, "/loop") is None
    assert response_shape(spec, "/ext") is None


# check_required


def test_object_with_all_required_fields_has_no_problems():
    assert (
        check_required({"name": "a", "photoUrls": []}, ResponseShape(False, ("name", "photoUrls")))
        == []
    )


def test_object_missing_fields_are_listed():
    problems = check_required({"name": "a"}, ResponseShape(False, ("name", "photoUrls")))
    assert problems == ["photoUrls"]


def test_array_reports_missing_fields_with_counts():
    data = [{"name": "a", "photoUrls": []}, {"photoUrls": []}, {"photoUrls": []}]
    problems = check_required(data, ResponseShape(True, ("name", "photoUrls")))
    assert problems == ["name (в 2 из 3 элементов)"]


def test_empty_array_has_nothing_to_check():
    assert check_required([], ResponseShape(True, ("name",))) is None


def test_wrong_top_level_type_is_reported():
    assert check_required({"a": 1}, ResponseShape(True, ("a",))) == ["ожидался массив"]
    assert check_required([1], ResponseShape(False, ("a",))) == ["ожидался объект"]
