from connectors.base import ChangeEvent


def test_to_item_maps_key_payload_and_format():
    event = ChangeEvent(
        op="upsert",
        key="public.users.42",
        payload={"name": "ada"},
        source_meta={"source": "postgres", "table": "users"},
        data_format="json",
    )
    assert event.to_item() == {
        "data_key": "public.users.42",
        "data": {"name": "ada"},
        "data_format": "json",
    }


def test_data_format_defaults_to_json():
    event = ChangeEvent(op="upsert", key="k", payload={"a": 1})
    assert event.data_format == "json"
    assert event.source_meta == {}


def test_text_payload_carries_text_format():
    event = ChangeEvent(op="upsert", key="docs/readme.md", payload="hello", data_format="text")
    assert event.to_item() == {
        "data_key": "docs/readme.md",
        "data": "hello",
        "data_format": "text",
    }
