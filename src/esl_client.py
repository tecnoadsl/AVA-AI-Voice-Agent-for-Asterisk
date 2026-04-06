"""FreeSWITCH ESL Inbound Client — replaces ARIClient for PBX control."""
import asyncio
import logging
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ESLInboundClient:
    """Async inbound ESL client for FreeSWITCH.

    Maintains a persistent connection to FreeSWITCH ESL for:
    - Subscribing to global events
    - Originating outbound calls
    - Executing commands on existing channels (uuid_* commands)
    """

    def __init__(self, host: str, port: int = 8021, password: str = "ClueCon"):
        self.host = host
        self.port = port
        self.password = password
        self.connected = False
        self._conn = None
        self.event_handlers: Dict[str, List[Callable]] = {}
        self._listener_task: Optional[asyncio.Task] = None

    async def connect(self):
        """Connect to FreeSWITCH ESL."""
        import greenswitch
        self._conn = greenswitch.InboundESL(host=self.host, port=self.port, password=self.password)
        await self._conn.connect()
        self.connected = True
        logger.info("ESL inbound connected to %s:%d", self.host, self.port)
        await self._conn.send("events plain ALL")

    async def disconnect(self):
        if self._listener_task:
            self._listener_task.cancel()
        if self._conn:
            self._conn.stop()
        self.connected = False
        logger.info("ESL inbound disconnected")

    def on_event(self, event_name: str, handler: Callable[[Dict], Awaitable[None]]):
        if event_name not in self.event_handlers:
            self.event_handlers[event_name] = []
        self.event_handlers[event_name].append(handler)

    async def start_listening(self):
        while True:
            try:
                if not self.connected:
                    await self.connect()
                async for event in self._conn:
                    event_name = event.get("Event-Name")
                    if event_name:
                        await self._dispatch_event(dict(event))
            except Exception as e:
                logger.error("ESL connection lost: %s. Reconnecting in 5s...", e)
                self.connected = False
                await asyncio.sleep(5)

    async def _dispatch_event(self, event: Dict[str, Any]):
        event_name = event.get("Event-Name")
        if event_name and event_name in self.event_handlers:
            for handler in self.event_handlers[event_name]:
                try:
                    await handler(event)
                except Exception as e:
                    logger.error("Event handler error for %s: %s", event_name, e)

    async def api(self, command: str) -> str:
        if not self._conn or not self.connected:
            raise ConnectionError("ESL not connected")
        result = await self._conn.api(command)
        return result.body if hasattr(result, "body") else str(result)

    async def bgapi(self, command: str) -> str:
        if not self._conn or not self.connected:
            raise ConnectionError("ESL not connected")
        result = await self._conn.bgapi(command)
        return result.body if hasattr(result, "body") else str(result)

    async def kill(self, uuid: str, cause: str = "NORMAL_CLEARING") -> str:
        return await self.api(f"uuid_kill {uuid} {cause}")

    async def transfer(self, uuid: str, destination: str,
                       dialplan: str = "XML", context: str = "default") -> str:
        return await self.api(f"uuid_transfer {uuid} {destination} {dialplan} {context}")

    async def bridge(self, uuid1: str, uuid2: str) -> str:
        return await self.api(f"uuid_bridge {uuid1} {uuid2}")

    async def broadcast(self, uuid: str, path: str, leg: str = "aleg") -> str:
        return await self.api(f"uuid_broadcast {uuid} {path} {leg}")

    async def send_dtmf(self, uuid: str, digits: str) -> str:
        return await self.api(f"uuid_send_dtmf {uuid} {digits}")

    async def get_channel_var(self, uuid: str, var_name: str) -> Optional[str]:
        result = await self.api(f"uuid_getvar {uuid} {var_name}")
        if result and not result.startswith("-ERR"):
            return result.strip()
        return None

    async def set_channel_var(self, uuid: str, var_name: str, value: str) -> str:
        return await self.api(f"uuid_setvar {uuid} {var_name} {value}")

    async def originate(self, url: str, extension: str, context: str = "default",
                        dialplan: str = "XML", cid_name: str = "", cid_num: str = "",
                        timeout: int = 30, channel_vars: Optional[Dict[str, str]] = None) -> str:
        var_str = ""
        if channel_vars:
            pairs = ",".join(f"{k}={v}" for k, v in channel_vars.items())
            var_str = f"{{{pairs}}}"
        cmd = (f"originate {var_str}{url} {extension} {dialplan} {context} "
               f"'{cid_name}' {cid_num} {timeout}")
        return await self.api(cmd)

    async def show_registrations(self, profile: str = "internal") -> str:
        return await self.api(f"sofia status profile {profile} reg")

    async def conference_list(self) -> str:
        return await self.api("conference list")
