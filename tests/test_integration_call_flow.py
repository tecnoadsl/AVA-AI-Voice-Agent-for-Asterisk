"""Integration test: simulates a full inbound call flow without real FreeSWITCH."""
import pytest
import asyncio
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_inbound_call_flow():
    """Simulate: FS connects via outbound ESL -> answer -> AudioSocket -> hangup."""
    from src.esl_outbound_server import OutboundCall

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock(body="+OK"))

    variables = {
        "Unique-ID": "call-uuid-001",
        "variable_domain_name": "db.voip",
        "variable_ai_provider": "litellm_hybrid",
        "variable_ai_context": "receptionist",
        "Caller-Caller-ID-Number": "+391234567890",
        "Caller-Caller-ID-Name": "Mario Rossi",
        "Caller-Destination-Number": "5900",
    }

    async def get_var(name):
        return variables.get(name)

    call = OutboundCall(conn=mock_conn, get_variable=get_var)
    await call.load_variables()

    assert call.uuid == "call-uuid-001"
    assert call.domain_name == "db.voip"
    assert call.caller_number == "+391234567890"

    await call.answer()
    mock_conn.execute.assert_any_call("answer")

    await call.start_audiosocket("93.189.136.90", 8090)
    calls = mock_conn.execute.call_args_list
    audiosocket_call = [c for c in calls if "audiosocket" in str(c).lower()]
    assert len(audiosocket_call) >= 1

    await call.hangup()
    mock_conn.execute.assert_any_call("hangup", "NORMAL_CLEARING")


@pytest.mark.asyncio
async def test_tenant_resolution_in_call():
    """Verify tenant config is correctly resolved from call domain."""
    from src.config_freeswitch import TenantResolver

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "db.voip.yaml"), "w") as f:
            f.write(
                "domain: db.voip\nprovider: litellm_hybrid\nlanguage: it\n"
                "tts_voice: it_male\ncontext_prompt: Assistente Tecnoadsl\n"
                "greeting: Buongiorno!\ntransfer_extensions:\n"
                "  vendite: '1001'\n  assistenza: '1002'\n"
            )
        with open(os.path.join(tmpdir, "default.yaml"), "w") as f:
            f.write(
                "domain: default\nprovider: local\nlanguage: en\n"
                "tts_voice: en_male\ncontext_prompt: Default\n"
            )

        resolver = TenantResolver(tmpdir)

        tenant = resolver.resolve("db.voip")
        assert tenant.provider == "litellm_hybrid"
        assert tenant.language == "it"
        assert tenant.transfer_extensions["vendite"] == "1001"

        tenant = resolver.resolve("unknown.domain")
        assert tenant.provider == "local"
        assert tenant.language == "en"

        tenant = resolver.resolve("db.voip", overrides={"provider": "openai_realtime"})
        assert tenant.provider == "openai_realtime"
        assert tenant.language == "it"


@pytest.mark.asyncio
async def test_esl_inbound_event_dispatch():
    """Verify ESL client dispatches events to registered handlers."""
    from src.esl_client import ESLInboundClient

    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    hangup_handler = AsyncMock()
    dtmf_handler = AsyncMock()

    client.on_event("CHANNEL_HANGUP_COMPLETE", hangup_handler)
    client.on_event("DTMF", dtmf_handler)

    await client._dispatch_event({
        "Event-Name": "CHANNEL_HANGUP_COMPLETE",
        "Unique-ID": "uuid-001",
        "Hangup-Cause": "NORMAL_CLEARING",
    })
    hangup_handler.assert_called_once()
    dtmf_handler.assert_not_called()

    await client._dispatch_event({
        "Event-Name": "DTMF",
        "Unique-ID": "uuid-001",
        "DTMF-Digit": "5",
    })
    dtmf_handler.assert_called_once()


@pytest.mark.asyncio
async def test_transfer_tool_uses_domain_context():
    """Verify transfer sends uuid_transfer with correct FusionPBX domain."""
    import sys
    sys.path.insert(0, ".")
    from tests.conftest import MockESLClient, MockToolContext, MockSession

    esl = MockESLClient()
    session = MockSession(domain_name="norcia.voip6")
    ctx = MockToolContext()
    ctx.esl_client = esl
    ctx._session = session
    ctx.domain_name = "norcia.voip6"
    ctx.caller_channel_id = "test-caller-channel"

    # Configure a valid destination so the tool can resolve it
    ctx.config = {
        "tools.transfer": {
            "destinations": {
                "2001": {
                    "type": "extension",
                    "target": "2001",
                    "description": "Interno 2001",
                }
            }
        }
    }

    from src.tools.telephony.unified_transfer import UnifiedTransferTool
    tool = UnifiedTransferTool()
    result = await tool.execute(
        {"destination": "2001", "type": "extension"},
        ctx,
    )

    # MockESLClient.transfer records: "transfer:{channel_id}:{destination}:{context}"
    transfer_cmd = [c for c in esl.commands_sent if c.startswith("transfer:")]
    assert len(transfer_cmd) == 1
    assert "norcia.voip6" in transfer_cmd[0]
    assert "2001" in transfer_cmd[0]
    assert result["status"] == "success"
