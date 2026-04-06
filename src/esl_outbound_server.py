"""Outbound ESL Server — handles inbound calls from FreeSWITCH dialplan socket() app."""
import asyncio
import logging
from typing import Any, Callable, Awaitable, Dict, Optional

logger = logging.getLogger(__name__)


class OutboundCall:
    """Represents a single inbound call received via outbound ESL."""

    def __init__(self, conn, get_variable: Callable):
        self.conn = conn
        self._get_variable = get_variable
        self.uuid: Optional[str] = None
        self.domain_name: Optional[str] = None
        self.ai_provider: Optional[str] = None
        self.ai_context: Optional[str] = None
        self.caller_number: Optional[str] = None
        self.caller_name: Optional[str] = None
        self.called_number: Optional[str] = None

    async def load_variables(self):
        self.uuid = await self._get_variable("Unique-ID")
        self.domain_name = await self._get_variable("variable_domain_name")
        self.ai_provider = await self._get_variable("variable_ai_provider")
        self.ai_context = await self._get_variable("variable_ai_context")
        self.caller_number = await self._get_variable("Caller-Caller-ID-Number")
        self.caller_name = await self._get_variable("Caller-Caller-ID-Name")
        self.called_number = await self._get_variable("Caller-Destination-Number")
        logger.info("Inbound call %s from %s to %s on domain %s",
                     self.uuid, self.caller_number, self.called_number, self.domain_name)

    async def answer(self):
        await self.conn.execute("answer")
        logger.info("Answered call %s", self.uuid)

    async def hangup(self, cause: str = "NORMAL_CLEARING"):
        await self.conn.execute("hangup", cause)
        logger.info("Hung up call %s (%s)", self.uuid, cause)

    async def start_audio_stream(self, ws_url: str):
        """Start WebSocket audio streaming via mod_audio_stream."""
        await self.conn.execute("audio_stream", f"{ws_url}/{self.uuid} mono 8000")
        logger.info("Audio stream started for call %s -> %s", self.uuid, ws_url)

    async def start_audiosocket(self, host: str, port: int):
        """Deprecated: use start_audio_stream instead.

        Kept for backwards compatibility with Asterisk AudioSocket dialplan.
        Translates host/port into a WebSocket URL and delegates to
        start_audio_stream.
        """
        logger.warning(
            "start_audiosocket is deprecated; use start_audio_stream with a ws:// URL"
        )
        ws_url = f"ws://{host}:{port}"
        await self.start_audio_stream(ws_url)

    async def playback(self, path: str):
        await self.conn.execute("playback", path)

    async def execute(self, app: str, args: str = ""):
        await self.conn.execute(app, args)

    async def set_variable(self, name: str, value: str):
        await self.conn.execute("set", f"{name}={value}")


class OutboundESLServer:
    """TCP server that accepts outbound ESL connections from FreeSWITCH."""

    def __init__(self, host: str, port: int,
                 on_call: Callable[["OutboundCall"], Awaitable[None]]):
        self.host = host
        self.port = port
        self.on_call = on_call
        self._server = None
        self.active_calls: Dict[str, OutboundCall] = {}

    async def start(self):
        import greenswitch
        self._server = await greenswitch.OutboundESLServer.create(
            self._handle_connection, self.host, self.port)
        logger.info("Outbound ESL server listening on %s:%d", self.host, self.port)

    async def _handle_connection(self, conn):
        call = None
        try:
            async def get_variable(name):
                return conn.get_header(name)
            call = OutboundCall(conn=conn, get_variable=get_variable)
            await call.load_variables()
            if call.uuid:
                self.active_calls[call.uuid] = call
            await self.on_call(call)
        except Exception as e:
            logger.error("Error handling outbound ESL connection: %s", e, exc_info=True)
        finally:
            if call and call.uuid and call.uuid in self.active_calls:
                del self.active_calls[call.uuid]

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Outbound ESL server stopped")
