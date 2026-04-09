"""WebSocket Audio Server for FreeSWITCH mod_audio_stream."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Callable, Awaitable, Dict, Optional
from urllib.parse import urlparse, parse_qs

import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)


class WSAudioServer:
    """WebSocket server for bidirectional audio with FreeSWITCH mod_audio_stream.

    Same callback interface as AudioSocketServer for drop-in replacement.
    Inbound audio arrives as binary WebSocket frames (L16/PCM).
    Outbound audio is sent as JSON ``streamAudio`` responses.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        on_connect: Callable[[str], Awaitable[bool]],       # (uuid) -> accept?
        on_audio: Callable[[str, bytes], Awaitable[None]],  # (uuid, pcm_bytes)
        on_disconnect: Optional[Callable[[str], Awaitable[None]]] = None,
        sample_rate: int = 8000,
    ) -> None:
        self.host = host
        self.port = port
        self.on_connect = on_connect
        self.on_audio = on_audio
        self.on_disconnect = on_disconnect
        self.sample_rate = sample_rate
        self._server = None
        self._connections: Dict[str, WebSocketServerProtocol] = {}
        self._pending_uuids: list = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle_connection, self.host, self.port,
            compression=None,  # Disable permessage-deflate; mod_audio_stream IXWebSocket may misbehave with it
        )
        logger.info("WS Audio server listening on ws://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("WS Audio server stopped")

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    async def _handle_connection(
        self, websocket: WebSocketServerProtocol, path: str = "/"
    ) -> None:
        # websockets >= 14 no longer passes `path` as second arg;
        # retrieve it from the request object when available.
        if path == "/" and hasattr(websocket, "request") and websocket.request:
            path = getattr(websocket.request, "path", path)
        uuid = self._extract_uuid(path)

        if not uuid:
            if self._pending_uuids:
                uuid = self._pending_uuids.pop(0)
                logger.info("WS connection matched to pending UUID %s", uuid)
            else:
                # Generate a temporary UUID; mod_audio_stream may send the
                # real channel UUID as a JSON text frame later.
                import uuid as _uuid_mod
                uuid = str(_uuid_mod.uuid4())
                logger.info("WS connection without UUID, assigned %s", uuid)

        logger.info("WS audio connection from UUID %s", uuid)

        accepted = await self.on_connect(uuid)
        if not accepted:
            logger.warning("WS connection rejected for UUID %s", uuid)
            await websocket.close(1008, "Connection rejected")
            return

        self._connections[uuid] = websocket
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Binary frame = PCM audio from FreeSWITCH
                    await self.on_audio(uuid, message)
                elif isinstance(message, str):
                    # Text frame = JSON metadata/control — log at debug level
                    try:
                        data = json.loads(message)
                        logger.debug(
                            "WS text frame from %s: type=%s", uuid, data.get("type", "unknown")
                        )
                    except json.JSONDecodeError:
                        logger.debug("WS non-JSON text frame from %s", uuid)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._connections.pop(uuid, None)
            if self.on_disconnect:
                await self.on_disconnect(uuid)
            logger.info("WS audio disconnected UUID %s", uuid)

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    async def send_audio(self, uuid: str, audio_data: bytes) -> bool:
        """Send PCM audio back to FreeSWITCH via mod_audio_stream streamAudio response."""
        ws = self._connections.get(uuid)
        if not ws:
            logger.debug("Attempted to send audio to unknown UUID %s", uuid)
            return False

        payload = json.dumps(
            {
                "type": "streamAudio",
                "data": {
                    "audioDataType": "raw",
                    "sampleRate": self.sample_rate,
                    "audioData": base64.b64encode(audio_data).decode("ascii"),
                },
            }
        )
        try:
            await ws.send(payload)
            return True
        except Exception as exc:
            logger.error("Failed to send audio to %s: %s", uuid, exc)
            return False

    async def close_connection(self, uuid: str) -> None:
        """Proactively close the WebSocket for a given UUID."""
        ws = self._connections.get(uuid)
        if ws:
            await ws.close()

    def expect_connection(self, uuid: str) -> None:
        """Register a UUID that we expect a WebSocket connection for."""
        if uuid not in self._pending_uuids:
            self._pending_uuids.append(uuid)
            logger.debug("Expecting WS connection for UUID %s", uuid)

    def get_connection_count(self) -> int:
        """Return number of active WebSocket connections."""
        return len(self._connections)

    async def disconnect(self, uuid: str) -> None:
        """Close the WebSocket for a given UUID (alias for close_connection)."""
        await self.close_connection(uuid)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_uuid(path: str) -> Optional[str]:
        """Extract UUID from URL path or query string.

        Supports:
          - /some-uuid-here
          - /?uuid=some-uuid-here
          - /path?uuid=some-uuid-here
        """
        parsed = urlparse(path)
        # Try path first: strip leading slash and any trailing query
        candidate = parsed.path.strip("/")
        if candidate:
            return candidate
        # Fall back to query param
        params = parse_qs(parsed.query)
        uuid_list = params.get("uuid", [])
        return uuid_list[0] if uuid_list else None
