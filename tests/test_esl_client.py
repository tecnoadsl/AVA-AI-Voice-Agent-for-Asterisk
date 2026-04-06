import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.esl_client import ESLInboundClient


@pytest.mark.asyncio
async def test_esl_client_init():
    client = ESLInboundClient(host="10.0.0.1", port=8021, password="secret")
    assert client.host == "10.0.0.1"
    assert client.port == 8021
    assert client.connected is False


@pytest.mark.asyncio
async def test_esl_client_registers_event_handler():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    handler = AsyncMock()
    client.on_event("CHANNEL_ANSWER", handler)
    assert "CHANNEL_ANSWER" in client.event_handlers
    assert handler in client.event_handlers["CHANNEL_ANSWER"]


@pytest.mark.asyncio
async def test_esl_client_dispatch_event():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    handler = AsyncMock()
    client.on_event("DTMF", handler)
    event = {"Event-Name": "DTMF", "Unique-ID": "test-uuid", "DTMF-Digit": "5"}
    await client._dispatch_event(event)
    handler.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_esl_client_api_formats_command():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    mock_conn = AsyncMock()
    mock_conn.api.return_value = MagicMock(body="+OK test-uuid")
    client._conn = mock_conn
    client.connected = True
    result = await client.api("uuid_kill test-uuid NORMAL_CLEARING")
    mock_conn.api.assert_called_once_with("uuid_kill test-uuid NORMAL_CLEARING")


@pytest.mark.asyncio
async def test_esl_client_originate():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    mock_conn = AsyncMock()
    mock_conn.api.return_value = MagicMock(body="+OK new-uuid")
    client._conn = mock_conn
    client.connected = True
    result = await client.originate(
        url="sofia/gateway/trunk/+391234567890",
        extension="5900", context="default", dialplan="XML",
        channel_vars={"domain_name": "db.voip", "ai_context": "receptionist"},
    )
    call_args = mock_conn.api.call_args[0][0]
    assert "originate" in call_args
    assert "domain_name=db.voip" in call_args
    assert "sofia/gateway/trunk/+391234567890" in call_args


@pytest.mark.asyncio
async def test_esl_client_uuid_transfer():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    mock_conn = AsyncMock()
    mock_conn.api.return_value = MagicMock(body="+OK")
    client._conn = mock_conn
    client.connected = True
    await client.transfer("test-uuid", "1001", context="db.voip")
    call_args = mock_conn.api.call_args[0][0]
    assert "uuid_transfer" in call_args
    assert "test-uuid" in call_args
    assert "1001" in call_args
    assert "db.voip" in call_args


@pytest.mark.asyncio
async def test_esl_client_uuid_kill():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    mock_conn = AsyncMock()
    mock_conn.api.return_value = MagicMock(body="+OK")
    client._conn = mock_conn
    client.connected = True
    await client.kill("test-uuid")
    mock_conn.api.assert_called_once_with("uuid_kill test-uuid NORMAL_CLEARING")


@pytest.mark.asyncio
async def test_esl_client_uuid_broadcast():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    mock_conn = AsyncMock()
    mock_conn.api.return_value = MagicMock(body="+OK")
    client._conn = mock_conn
    client.connected = True
    await client.broadcast("test-uuid", "/tmp/greeting.wav", leg="aleg")
    mock_conn.api.assert_called_once_with("uuid_broadcast test-uuid /tmp/greeting.wav aleg")


@pytest.mark.asyncio
async def test_esl_client_uuid_bridge():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    mock_conn = AsyncMock()
    mock_conn.api.return_value = MagicMock(body="+OK")
    client._conn = mock_conn
    client.connected = True
    await client.bridge("uuid-1", "uuid-2")
    mock_conn.api.assert_called_once_with("uuid_bridge uuid-1 uuid-2")


@pytest.mark.asyncio
async def test_esl_client_get_channel_var():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    mock_conn = AsyncMock()
    mock_conn.api.return_value = MagicMock(body="db.voip")
    client._conn = mock_conn
    client.connected = True
    result = await client.get_channel_var("test-uuid", "domain_name")
    mock_conn.api.assert_called_once_with("uuid_getvar test-uuid domain_name")
    assert result == "db.voip"
