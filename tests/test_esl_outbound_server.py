import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.esl_outbound_server import OutboundESLServer, OutboundCall


@pytest.mark.asyncio
async def test_outbound_server_init():
    handler = AsyncMock()
    server = OutboundESLServer(host="0.0.0.0", port=8085, on_call=handler)
    assert server.host == "0.0.0.0"
    assert server.port == 8085


@pytest.mark.asyncio
async def test_outbound_call_reads_channel_vars():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock(body="+OK"))

    async def fake_get_var(var_name):
        variables = {
            "variable_domain_name": "db.voip",
            "variable_ai_provider": "litellm_hybrid",
            "variable_ai_context": "receptionist",
            "Caller-Caller-ID-Number": "+391234567890",
            "Caller-Caller-ID-Name": "Test User",
            "Caller-Destination-Number": "5900",
            "Unique-ID": "test-uuid-123",
        }
        return variables.get(var_name)

    call = OutboundCall(conn=mock_conn, get_variable=fake_get_var)
    await call.load_variables()

    assert call.uuid == "test-uuid-123"
    assert call.domain_name == "db.voip"
    assert call.ai_provider == "litellm_hybrid"
    assert call.ai_context == "receptionist"
    assert call.caller_number == "+391234567890"
    assert call.called_number == "5900"


@pytest.mark.asyncio
async def test_outbound_call_answer():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock(body="+OK"))
    call = OutboundCall(conn=mock_conn, get_variable=AsyncMock(return_value=None))
    call.uuid = "test-uuid"
    await call.answer()
    mock_conn.execute.assert_called_once_with("answer")


@pytest.mark.asyncio
async def test_outbound_call_start_audiosocket():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock(body="+OK"))
    call = OutboundCall(conn=mock_conn, get_variable=AsyncMock(return_value=None))
    call.uuid = "test-uuid"
    await call.start_audiosocket("93.189.136.90", 8090)
    args = mock_conn.execute.call_args[0]
    assert "audiosocket" in str(args).lower()


@pytest.mark.asyncio
async def test_outbound_call_hangup():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock(body="+OK"))
    call = OutboundCall(conn=mock_conn, get_variable=AsyncMock(return_value=None))
    call.uuid = "test-uuid"
    await call.hangup()
    mock_conn.execute.assert_called_with("hangup", "NORMAL_CLEARING")
