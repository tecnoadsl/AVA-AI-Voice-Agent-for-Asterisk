"""Tests for WSAudioServer (WebSocket audio server for mod_audio_stream)."""

import base64
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.audio.ws_audio_server import WSAudioServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(**kwargs) -> WSAudioServer:
    defaults = dict(
        host="0.0.0.0",
        port=8090,
        on_connect=AsyncMock(return_value=True),
        on_audio=AsyncMock(),
    )
    defaults.update(kwargs)
    return WSAudioServer(**defaults)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_audio_server_init():
    server = _make_server()
    assert server.host == "0.0.0.0"
    assert server.port == 8090
    assert server.sample_rate == 8000


@pytest.mark.asyncio
async def test_ws_audio_server_custom_sample_rate():
    server = _make_server(sample_rate=16000)
    assert server.sample_rate == 16000


# ---------------------------------------------------------------------------
# send_audio — format validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_audio_server_send_audio_format():
    """send_audio must produce a correct mod_audio_stream streamAudio JSON payload."""
    server = _make_server(sample_rate=8000)

    mock_ws = AsyncMock()
    server._connections["test-uuid"] = mock_ws

    pcm_data = b"\x00\x01" * 160  # 320 bytes = 20 ms at 8 kHz 16-bit
    result = await server.send_audio("test-uuid", pcm_data)

    assert result is True
    mock_ws.send.assert_called_once()

    sent_text = mock_ws.send.call_args[0][0]
    sent = json.loads(sent_text)

    assert sent["type"] == "streamAudio"
    assert sent["data"]["audioDataType"] == "raw"
    assert sent["data"]["sampleRate"] == 8000

    # Verify base64 roundtrip
    decoded = base64.b64decode(sent["data"]["audioData"])
    assert decoded == pcm_data


@pytest.mark.asyncio
async def test_ws_audio_server_send_audio_uses_configured_sample_rate():
    server = _make_server(sample_rate=16000)
    mock_ws = AsyncMock()
    server._connections["test-uuid"] = mock_ws

    await server.send_audio("test-uuid", b"\x00" * 640)

    sent = json.loads(mock_ws.send.call_args[0][0])
    assert sent["data"]["sampleRate"] == 16000


# ---------------------------------------------------------------------------
# send_audio — missing connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_audio_server_send_audio_no_connection():
    server = _make_server()
    result = await server.send_audio("nonexistent-uuid", b"\x00\x01")
    assert result is False


# ---------------------------------------------------------------------------
# send_audio — exception propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_audio_server_send_audio_ws_error_returns_false():
    server = _make_server()
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = RuntimeError("connection lost")
    server._connections["test-uuid"] = mock_ws

    result = await server.send_audio("test-uuid", b"\x00\x01")
    assert result is False


# ---------------------------------------------------------------------------
# close_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_audio_server_close_connection():
    server = _make_server()
    mock_ws = AsyncMock()
    server._connections["test-uuid"] = mock_ws

    await server.close_connection("test-uuid")
    mock_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_ws_audio_server_close_nonexistent_connection():
    """Closing a non-existent UUID must not raise."""
    server = _make_server()
    await server.close_connection("ghost-uuid")  # should not raise


# ---------------------------------------------------------------------------
# UUID extraction from path
# ---------------------------------------------------------------------------


def test_extract_uuid_from_path():
    assert WSAudioServer._extract_uuid("/abc-def-123") == "abc-def-123"


def test_extract_uuid_from_query_param():
    assert WSAudioServer._extract_uuid("/?uuid=abc-def-123") == "abc-def-123"


def test_extract_uuid_from_path_with_query():
    assert WSAudioServer._extract_uuid("/abc-def-123?foo=bar") == "abc-def-123"


def test_extract_uuid_root_path_no_param():
    assert WSAudioServer._extract_uuid("/") is None


def test_extract_uuid_empty_path():
    assert WSAudioServer._extract_uuid("") is None


# ---------------------------------------------------------------------------
# _handle_connection — UUID rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_connection_rejects_missing_uuid():
    server = _make_server()
    mock_ws = AsyncMock()

    await server._handle_connection(mock_ws, "/")

    mock_ws.close.assert_called_once_with(1008, "UUID required")
    server.on_connect.assert_not_called()


@pytest.mark.asyncio
async def test_handle_connection_rejects_when_on_connect_returns_false():
    server = _make_server(on_connect=AsyncMock(return_value=False))
    mock_ws = AsyncMock()

    await server._handle_connection(mock_ws, "/some-uuid")

    mock_ws.close.assert_called_once_with(1008, "Connection rejected")


# ---------------------------------------------------------------------------
# _handle_connection — binary audio routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_connection_routes_binary_frames_to_on_audio():
    on_audio = AsyncMock()
    server = _make_server(on_audio=on_audio)

    pcm_chunk = b"\x01\x02" * 80

    import websockets.exceptions

    class _FakeAsyncIter:
        def __init__(self):
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._sent:
                self._sent = True
                return pcm_chunk
            raise websockets.exceptions.ConnectionClosed(None, None)

    mock_ws = AsyncMock()
    type(mock_ws).__aiter__ = lambda self_: _FakeAsyncIter()

    await server._handle_connection(mock_ws, "/some-uuid")

    on_audio.assert_awaited_once_with("some-uuid", pcm_chunk)


# ---------------------------------------------------------------------------
# _handle_connection — on_disconnect callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_connection_calls_on_disconnect():
    on_disconnect = AsyncMock()
    server = _make_server(on_disconnect=on_disconnect)

    import websockets.exceptions

    class _FakeAsyncIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise websockets.exceptions.ConnectionClosed(None, None)

    mock_ws = AsyncMock()
    mock_ws.__aiter__ = lambda: _FakeAsyncIter().__aiter__()

    # Patch iteration so the async-for in _handle_connection uses our iterator
    type(mock_ws).__aiter__ = lambda self_: _FakeAsyncIter()

    await server._handle_connection(mock_ws, "/some-uuid")

    on_disconnect.assert_awaited_once_with("some-uuid")
