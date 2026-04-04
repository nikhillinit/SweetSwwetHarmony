import pytest

from discovery_engine import mcp_server


@pytest.mark.asyncio
async def test_push_to_notion_dry_run_skips_connector_and_gate(monkeypatch):
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)

    def fail_connector():
        raise AssertionError("dry-run should not initialize the Notion connector")

    def fail_gate():
        raise AssertionError("dry-run should not initialize the verification gate")

    monkeypatch.setattr(mcp_server, "get_notion_connector", fail_connector)
    monkeypatch.setattr(mcp_server, "get_verification_gate", fail_gate)

    result = await mcp_server._handle_push_to_notion(
        {"discovery_id": "abc123", "dry_run": "true"}
    )

    text = result.messages[0].content.text
    assert "Dry run - would push to Notion" in text
    assert '"status": "dry_run"' in text
    assert '"discovery_id": "abc123"' in text
