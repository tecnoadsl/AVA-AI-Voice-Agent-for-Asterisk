"""ARI-to-ESL compatibility shim.

Wraps ESLInboundClient to expose the same interface that engine.py expects
from ARIClient.  This avoids rewriting hundreds of call-sites in the 14 000-line
engine while we migrate from Asterisk/ARI to FreeSWITCH/ESL.

Methods that have no FreeSWITCH equivalent (bridges, ExternalMedia, etc.) are
stubbed as no-ops or raise NotImplementedError with a clear message.
"""

import asyncio
import logging
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ARICompatClient:
    """Drop-in replacement for ARIClient backed by ESLInboundClient.

    The engine references ``self.ari_client.<method>`` in ~150 places.
    This adapter translates those calls to ESL equivalents so the rest of
    the engine can keep working with minimal changes.
    """

    def __init__(self, esl_client):
        self._esl = esl_client
        self.engine = None  # engine sets this after construction
        self.running = False
        self.is_connected = False
        # ARI tracks active playbacks; we keep a dict for compat but it's unused with ESL
        self.active_playbacks: Dict[str, str] = {}
        # Event handlers (ARI-style event names)
        self._event_handlers: Dict[str, List[Callable]] = {}

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def start_listening(self):
        """Start ESL event loop (replaces ARI WebSocket listener)."""
        self.running = True
        self.is_connected = True
        try:
            await self._esl.start_listening()
        finally:
            self.running = False
            self.is_connected = False

    async def disconnect(self):
        await self._esl.disconnect()
        self.running = False
        self.is_connected = False

    # ------------------------------------------------------------------
    # Event registration (translates ARI event names to ESL)
    # ------------------------------------------------------------------

    _ARI_TO_ESL_EVENT = {
        "StasisStart": None,  # replaced by outbound ESL server
        "StasisEnd": "CHANNEL_HANGUP_COMPLETE",
        "ChannelDestroyed": "CHANNEL_DESTROY",
        "ChannelDtmfReceived": "DTMF",
        "ChannelVarset": "CHANNEL_DATA",  # approximate; engine handler adapted
        "ChannelTalkingStarted": "DETECTED_SPEECH",  # no direct FS equivalent
        "ChannelTalkingFinished": "DETECTED_SILENCE",  # no direct FS equivalent
        "PlaybackFinished": None,  # handled differently in FS
    }

    def on_event(self, ari_event_name: str, handler: Callable):
        """Register handler using ARI event name; maps to ESL internally."""
        esl_name = self._ARI_TO_ESL_EVENT.get(ari_event_name)
        if esl_name:
            # Wrap handler to translate ESL event dict into ARI-like structure
            wrapper = self._make_ari_compat_wrapper(ari_event_name, handler)
            self._esl.on_event(esl_name, wrapper)
        elif ari_event_name == "StasisStart":
            # StasisStart is handled by outbound ESL server, not inbound events
            logger.debug("StasisStart handler registered but will be called by outbound server, not ESL events")
        else:
            logger.debug("ARI event %s has no ESL equivalent; handler ignored", ari_event_name)

        # Also store in local registry for add_event_handler()
        if ari_event_name not in self._event_handlers:
            self._event_handlers[ari_event_name] = []
        self._event_handlers[ari_event_name].append(handler)

    def add_event_handler(self, ari_event_name: str, handler: Callable):
        """Alias for on_event (ARI client uses both names)."""
        self.on_event(ari_event_name, handler)

    def _make_ari_compat_wrapper(self, ari_event_name: str, handler: Callable):
        """Create a wrapper that translates ESL event dict to ARI-like dict."""
        async def wrapper(esl_event: dict):
            ari_event = self._esl_to_ari_event(ari_event_name, esl_event)
            await handler(ari_event)
        return wrapper

    @staticmethod
    def _esl_to_ari_event(ari_name: str, esl: dict) -> dict:
        """Best-effort translation of ESL event fields to ARI-like structure."""
        uuid = esl.get("Unique-ID", "")
        channel_name = esl.get("Channel-Name", "")
        caller_name = esl.get("Caller-Caller-ID-Name", "")
        caller_number = esl.get("Caller-Caller-ID-Number", "")

        channel_dict = {
            "id": uuid,
            "name": channel_name,
            "caller": {"name": caller_name, "number": caller_number},
        }

        if ari_name == "ChannelDtmfReceived":
            return {"channel": channel_dict, "digit": esl.get("DTMF-Digit", "")}
        if ari_name == "ChannelVarset":
            return {
                "channel": channel_dict,
                "variable": esl.get("variable_name", ""),
                "value": esl.get("variable_value", ""),
            }
        # Default: wrap channel
        return {"channel": channel_dict}

    # ------------------------------------------------------------------
    # Channel operations (translated to ESL)
    # ------------------------------------------------------------------

    async def answer_channel(self, channel_id: str):
        """Answer is handled by outbound ESL; this is a no-op for inbound calls."""
        logger.debug("answer_channel called (no-op in ESL mode, answered by outbound server): %s", channel_id)

    async def hangup_channel(self, channel_id: str):
        try:
            await self._esl.kill(channel_id)
        except Exception as e:
            logger.warning("ESL kill failed for %s: %s", channel_id, e)

    async def set_channel_var(self, channel_id: str, var_name: str, value: str):
        try:
            await self._esl.set_channel_var(channel_id, var_name, value)
        except Exception as e:
            logger.debug("ESL set_channel_var failed for %s %s: %s", channel_id, var_name, e)

    async def play_media(self, channel_id: str, media_uri: str) -> Optional[dict]:
        """Play media on channel. Translates ARI media URI to FS path."""
        path = self._translate_media_uri(media_uri)
        try:
            await self._esl.broadcast(channel_id, path)
            return {"id": f"playback-{channel_id}"}
        except Exception as e:
            logger.warning("ESL broadcast failed for %s: %s", channel_id, e)
            return None

    async def play_media_on_channel_with_id(self, channel_id: str, media_uri: str, playback_id: str) -> bool:
        """Play media with specific playback ID."""
        path = self._translate_media_uri(media_uri)
        try:
            await self._esl.broadcast(channel_id, path)
            return True
        except Exception:
            return False

    async def play_sound(self, channel_id: str, sound: str):
        """Play a sound file."""
        try:
            await self._esl.broadcast(channel_id, sound)
        except Exception as e:
            logger.warning("ESL play_sound failed for %s: %s", channel_id, e)

    async def stop_playback(self, playback_id: str):
        """Stop playback — approximate via uuid_break."""
        # playback_id format: "playback-<uuid>"
        uuid = playback_id.replace("playback-", "") if playback_id.startswith("playback-") else playback_id
        try:
            await self._esl.api(f"uuid_break {uuid} all")
        except Exception as e:
            logger.debug("ESL stop_playback failed: %s", e)

    async def record_channel(self, channel_id: str, **kwargs) -> bool:
        """Start recording on channel via uuid_record."""
        name = kwargs.get("name", f"recording-{channel_id}")
        fmt = kwargs.get("format", "wav")
        path = f"/tmp/{name}.{fmt}"
        try:
            await self._esl.api(f"uuid_record {channel_id} start {path}")
            return True
        except Exception:
            return False

    async def continue_in_dialplan(self, channel_id: str, context: str = "default",
                                    extension: str = "s", priority: int = 1, **kwargs) -> bool:
        """Transfer channel back to dialplan."""
        try:
            await self._esl.transfer(channel_id, f"{extension} XML {context}")
            return True
        except Exception as e:
            logger.warning("ESL transfer failed for %s: %s", channel_id, e)
            return False

    async def originate_channel(self, **kwargs) -> Optional[dict]:
        """Originate a new channel."""
        endpoint = kwargs.get("endpoint", "")
        extension = kwargs.get("extension", "s")
        context = kwargs.get("context", "default")
        caller_id = kwargs.get("callerId", "")
        timeout = kwargs.get("timeout", 30)
        variables = kwargs.get("variables", {})

        cid_name = ""
        cid_num = ""
        if caller_id:
            # ARI format: "Name" <number>
            import re
            m = re.match(r'"?([^"]*)"?\s*<(\d+)>', caller_id)
            if m:
                cid_name, cid_num = m.group(1), m.group(2)
            else:
                cid_num = caller_id

        try:
            result = await self._esl.originate(
                url=endpoint,
                extension=extension,
                context=context,
                cid_name=cid_name,
                cid_num=cid_num,
                timeout=timeout,
                channel_vars=variables if isinstance(variables, dict) else None,
            )
            # Return ARI-like response
            return {"id": result.strip() if result else "", "name": endpoint}
        except Exception as e:
            logger.error("ESL originate failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Bridge operations (no-ops in FreeSWITCH — audio via AudioSocket)
    # ------------------------------------------------------------------

    async def create_bridge(self, bridge_type: str = "mixing") -> Optional[str]:
        """No bridges in FreeSWITCH AudioSocket mode. Return a fake ID."""
        import uuid as _uuid
        fake_id = f"fs-bridge-{_uuid.uuid4().hex[:8]}"
        logger.debug("create_bridge called (no-op in ESL mode), returning fake ID: %s", fake_id)
        return fake_id

    async def add_channel_to_bridge(self, bridge_id: str, channel_id: str) -> bool:
        """No-op — audio routing handled by AudioSocket/outbound ESL."""
        logger.debug("add_channel_to_bridge called (no-op): bridge=%s channel=%s", bridge_id, channel_id)
        return True

    async def remove_channel_from_bridge(self, bridge_id: str, channel_id: str) -> bool:
        logger.debug("remove_channel_from_bridge called (no-op): bridge=%s channel=%s", bridge_id, channel_id)
        return True

    async def destroy_bridge(self, bridge_id: str) -> bool:
        logger.debug("destroy_bridge called (no-op): %s", bridge_id)
        return True

    # ------------------------------------------------------------------
    # ExternalMedia (not used in FreeSWITCH — replaced by AudioSocket)
    # ------------------------------------------------------------------

    async def create_external_media_channel(self, **kwargs) -> Optional[dict]:
        """ExternalMedia is Asterisk-specific; not used with FreeSWITCH."""
        logger.debug("create_external_media_channel called (no-op in ESL mode)")
        return None

    # ------------------------------------------------------------------
    # Generic send_command (translates common ARI REST patterns to ESL)
    # ------------------------------------------------------------------

    async def send_command(self, method: str = "GET", resource: str = "",
                           params: Optional[dict] = None,
                           tolerate_statuses: Optional[list] = None,
                           **kwargs) -> Optional[dict]:
        """Translate ARI REST commands to ESL equivalents."""
        params = params or {}

        # GET channels/{id}/variable → uuid_getvar
        if method == "GET" and "/variable" in resource:
            parts = resource.split("/")
            # channels/{uuid}/variable
            if len(parts) >= 3:
                uuid = parts[1]
                var_name = params.get("variable", "")
                try:
                    value = await self._esl.get_channel_var(uuid, var_name)
                    return {"value": value or ""}
                except Exception:
                    return {"value": ""}

        # DELETE channels/{id}/moh → uuid_break (stop MOH)
        if method == "DELETE" and "/moh" in resource:
            parts = resource.split("/")
            if len(parts) >= 2:
                uuid = parts[1]
                try:
                    await self._esl.api(f"uuid_break {uuid} all")
                except Exception:
                    pass
            return {}

        # POST channels (originate)
        if method == "POST" and resource == "channels":
            return await self.originate_channel(**params)

        # POST channels/{id}/play → broadcast
        if method == "POST" and "/play" in resource:
            parts = resource.split("/")
            if len(parts) >= 2:
                uuid = parts[1]
                media = params.get("media", "")
                path = self._translate_media_uri(media)
                try:
                    await self._esl.broadcast(uuid, path)
                    return {"id": f"playback-{uuid}"}
                except Exception:
                    return None

        # POST channels/{id}/moh → uuid_broadcast with MOH
        if method == "POST" and "/moh" in resource:
            parts = resource.split("/")
            if len(parts) >= 2:
                uuid = parts[1]
                try:
                    await self._esl.api(f"uuid_hold on {uuid}")
                except Exception:
                    pass
            return {}

        # Fallback: log and return empty
        logger.debug(
            "send_command not translated: method=%s resource=%s params=%s",
            method, resource, params,
        )
        return {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _translate_media_uri(uri: str) -> str:
        """Convert ARI media URI to FreeSWITCH file path.

        ARI uses: sound:something or recording:path
        FS uses: /path/to/file.wav or local_stream://moh
        """
        if not uri:
            return uri
        if uri.startswith("sound:"):
            name = uri[6:]
            return f"/usr/share/freeswitch/sounds/en/us/callie/{name}.wav"
        if uri.startswith("recording:"):
            return uri[10:]
        # Already a path
        return uri
