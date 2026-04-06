# AVA AI Voice Agent - Source Code Reference for FreeSWITCH Port

> Extracted from https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk (main branch)
> Date: 2026-04-06

---

## 1. Architecture Overview

**Components:**
- `ai_engine` container: Python 3.11 app (`main.py` -> `Engine` class in `src/engine.py`)
- `local_ai_server` container: Local STT/LLM/TTS (Vosk/Sherpa/Kokoro/Piper)
- `admin_ui` container: FastAPI admin panel on port 3003
- All run with `network_mode: host`

**Call Flow (Asterisk):**
1. Dialplan routes call to `Stasis(asterisk-ai-voice-agent)`
2. Engine receives `StasisStart` via ARI WebSocket
3. Engine answers channel, creates mixing bridge, adds caller
4. Engine originates AudioSocket channel (or ExternalMedia/RTP)
5. AudioSocket server receives bidirectional PCM audio
6. Audio goes to AI provider (Deepgram/OpenAI/Google/Local)
7. Provider responses streamed back via AudioSocket

**Key interfaces to replace:**
- ARI WebSocket (events) -> FreeSWITCH ESL (events)
- ARI HTTP (commands) -> FreeSWITCH ESL API commands
- AudioSocket channel -> FreeSWITCH `mod_audio_fork` or raw TCP audio

---

## 2. ARIClient (`src/ari_client.py`)

### Class: `ARIClient`

```python
class ARIClient:
    def __init__(self, username, password, base_url, app_name, ssl_verify=True):
        # Builds WS URL: ws(s)://host/ari/events?api_key=user:pass&app=name&subscribeAll=true
        self.event_handlers: Dict[str, List[Callable]] = {}
        self.active_playbacks: Dict[str, str] = {}
        self.audio_frame_handler: Optional[Callable] = None
```

### Key Methods (all async):

| Method | ARI Equivalent | FreeSWITCH Equivalent |
|--------|---------------|----------------------|
| `connect()` | HTTP GET /asterisk/info + WS connect | ESL connect (inbound) |
| `start_listening()` | WS event loop with reconnect | ESL event loop |
| `disconnect()` | Close WS + HTTP session | ESL disconnect |
| `add_event_handler(event_type, handler)` | Register callback per event | ESL event filter + callback |
| `answer_channel(channel_id)` | POST /channels/{id}/answer | `uuid_answer` |
| `hangup_channel(channel_id)` | DELETE /channels/{id} | `uuid_kill` |
| `create_bridge(bridge_type="mixing")` | POST /bridges | Conference or `uuid_bridge` |
| `add_channel_to_bridge(bridge_id, channel_id)` | POST /bridges/{id}/addChannel | Conference member add |
| `remove_channel_from_bridge(bridge_id, channel_id)` | POST /bridges/{id}/removeChannel | Conference member kick |
| `originate_channel(endpoint, app, app_args, timeout, caller_id, channel_vars)` | POST /channels | `originate` |
| `continue_in_dialplan(channel_id, context, extension, priority)` | POST /channels/{id}/continue | `uuid_transfer` |
| `set_channel_var(channel_id, variable, value)` | POST /channels/{id}/variable | `uuid_setvar` |
| `play_media(channel_id, media_uri)` | POST /channels/{id}/play | `uuid_broadcast` or `playback` |
| `play_sound(channel_id, sound_file)` | Wraps play_media with sound: prefix | `uuid_broadcast` |
| `send_command(method, resource, data, params, tolerate_statuses)` | Generic ARI HTTP | ESL api/bgapi |
| `create_external_media_channel(app, external_host, format, direction, encapsulation)` | POST /channels/externalMedia | Not needed (use audio_fork) |

### Event dispatch pattern:
```python
# WS message loop
async for message in self.websocket:
    event_data = json.loads(message)
    event_type = event_data.get("type")
    if event_type in self.event_handlers:
        for handler in self.event_handlers[event_type]:
            asyncio.create_task(handler(event_data))
```

---

## 3. Engine (`src/engine.py`) - Key Patterns

### Event Registration (line ~540):
```python
self.ari_client.on_event("StasisStart", self._handle_stasis_start)
self.ari_client.on_event("StasisEnd", self._handle_stasis_end)
self.ari_client.on_event("ChannelDestroyed", self._handle_channel_destroyed)
self.ari_client.on_event("ChannelDtmfReceived", self._handle_dtmf_received)
self.ari_client.on_event("ChannelVarset", self._handle_channel_varset)
self.ari_client.on_event("ChannelTalkingStarted", self._handle_channel_talking_started)
self.ari_client.on_event("ChannelTalkingFinished", self._handle_channel_talking_finished)
```

**FreeSWITCH equivalents:**
- `StasisStart` -> `CHANNEL_ANSWER` (or custom `CUSTOM` event when app is invoked)
- `StasisEnd` -> `CHANNEL_HANGUP_COMPLETE`
- `ChannelDestroyed` -> `CHANNEL_DESTROY`
- `ChannelDtmfReceived` -> `DTMF`
- `ChannelVarset` -> `CHANNEL_DATA` / var access via ESL
- `ChannelTalkingStarted/Finished` -> No direct equivalent (use VAD or energy detection)

### Channel Type Detection:
```python
def _is_caller_channel(self, channel): 
    # name starts with SIP/, PJSIP/, DAHDI/, IAX2/, Dongle/
def _is_local_channel(self, channel):
    # name starts with Local/
def _is_audiosocket_channel(self, channel):
    # name starts with AudioSocket/
def _is_external_media_channel(self, channel):
    # name starts with UnicastRTP/
```

### StasisStart Handler (line 2373):
Routes by channel type and args:
1. If args contain "outbound" or "outbound_amd" -> `_handle_outbound_stasis()`
2. If args contain action_type -> `_handle_agent_action_stasis()` (transfer, voicemail, queue, bgm)
3. Caller channel (PJSIP/SIP) -> `_handle_caller_stasis_start_hybrid()`
4. Local channel -> `_handle_local_stasis_start_hybrid()`
5. AudioSocket channel -> `_handle_audiosocket_channel_stasis_start()`
6. ExternalMedia (UnicastRTP) -> `_handle_external_media_stasis_start()`

### Main Call Setup (`_handle_caller_stasis_start_hybrid`, line 2983):
```
Step 1: Answer channel (inbound only; outbound already answered)
Step 2: Create mixing bridge (ari_client.create_bridge())
Step 3: Add caller to bridge
Step 4: Create CallSession, read channel vars (AI_CONTEXT, DIALED_NUMBER, caller info)
Step 5: Originate AudioSocket channel (or ExternalMedia/RTP) 
Step 6: Resolve provider/pipeline, start provider session
```

Key channel variables read from Asterisk:
- `AI_CONTEXT` - which context/prompt to use
- `AI_PROVIDER` - override provider per call
- `DIALED_NUMBER` / `__FROM_DID` - called number
- `AAVA_OUTBOUND` - outbound flag
- `AAVA_ATTEMPT_ID`, `AAVA_CAMPAIGN_ID`, `AAVA_LEAD_ID` - outbound metadata

### Agent Action Handler (line 3648):
Routes action types from Stasis args:
```python
handlers = {
    'transfer': self._handle_transfer_answered,
    'warm-transfer': self._handle_transfer_answered,
    'attended-transfer': self._handle_attended_transfer_answered,
    'transfer-failed': self._handle_transfer_failed,
    'voicemail-complete': self._handle_voicemail_complete,
    'queue-answered': self._handle_queue_answered,
    'queue-failed': self._handle_queue_failed,
    'bgm': self._handle_background_music_channel,
}
```

Transfer answered flow:
1. Remove AI audio channel (ExternalMedia/AudioSocket) from bridge
2. Stop AI provider session
3. Add SIP agent channel to bridge (Caller <-> Agent direct)

### Cleanup (`_cleanup_call`, line 5483):
- Dedup guard with `_cleanup_in_progress` set and `_cleanup_completed_at` dict (TTL)
- Stops streaming playback, background music
- Cancels per-call background tasks
- Stops provider session
- Destroys bridge, hangs up channels
- Runs post-call tools (email summary, webhooks)
- Records call history to SQLite

---

## 4. AudioSocket Server (`src/audio/audiosocket_server.py`)

### TLV Protocol:
```
Header: 1 byte type + 2 bytes length (big-endian)
Types:
  0x00 = TERMINATE
  0x01 = UUID (handshake)
  0x03 = DTMF
  0x10 = AUDIO
  0xFF = ERROR
```

### Class: `AudioSocketServer`
```python
class AudioSocketServer:
    def __init__(self, host, port, *,
                 on_uuid: Callable[[str, str], Awaitable[bool]],    # (conn_id, uuid) -> accept?
                 on_audio: Callable[[str, bytes], Awaitable[None]],  # (conn_id, pcm_bytes)
                 on_disconnect: Optional[Callable[[str], Awaitable[None]]],
                 on_dtmf: Optional[Callable[[str, str], Awaitable[None]]]):
```

**Lifecycle:** `start()` -> `asyncio.start_server` on host:port -> `_handle_client` per connection

**Connection flow:**
1. First frame MUST be UUID (handshake)
2. `on_uuid` callback validates and maps conn_id to call
3. Subsequent TYPE_AUDIO frames -> `on_audio` callback
4. TYPE_DTMF frames -> `on_dtmf` callback

**Sending audio back:**
```python
async def send_audio(self, conn_id: str, audio_payload: bytes) -> bool:
    frame = bytes([TYPE_AUDIO]) + len(audio_payload).to_bytes(2, "big") + audio_payload
    writer.write(frame)
    await writer.drain()
```

**For FreeSWITCH:** This server can likely be reused as-is if FreeSWITCH supports AudioSocket (`mod_audiosocket`). Alternatively, replace with `mod_audio_fork` WebSocket or raw TCP.

---

## 5. Telephony Tools (`src/tools/telephony/`)

### Files:
- `__init__.py`
- `hangup.py` - `HangupCallTool` (tool name: `hangup_call`)
- `transfer.py` - `TransferCallTool` (tool name: `transfer_call`, DEPRECATED)
- `unified_transfer.py` - `UnifiedTransferTool` (tool name: `blind_transfer`)
- `live_agent_transfer.py` - `LiveAgentTransferTool` (tool name: `live_agent_transfer`)
- `attended_transfer.py` - Attended transfer with agent screening
- `cancel_transfer.py` - Cancel pending transfer
- `check_extension_status.py` - Check if extension is available
- `queue_transfer.py` - Queue-based transfer
- `voicemail.py` - Leave voicemail
- `hangup_policy.py` - Policy for auto-hangup behavior

### Base Tool class (`src/tools/base.py`):
```python
class Tool(ABC):
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition: ...
    
    @abstractmethod
    async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]: ...

@dataclass
class ToolDefinition:
    name: str
    description: str
    category: ToolCategory  # TELEPHONY, BUSINESS, HYBRID
    parameters: List[ToolParameter]
    input_schema: Optional[Dict[str, Any]] = None
    requires_channel: bool = False
    max_execution_time: int = 30
    phase: ToolPhase = ToolPhase.IN_CALL  # PRE_CALL, IN_CALL, POST_CALL

@dataclass
class ToolParameter:
    name: str
    type: str  # "string", "integer", "boolean", etc.
    description: str
    required: bool = False
    enum: Optional[List[str]] = None
    default: Optional[Any] = None
```

### ToolExecutionContext (`src/tools/context.py`):
```python
@dataclass
class ToolExecutionContext:
    call_id: str
    caller_channel_id: Optional[str]
    bridge_id: Optional[str]
    caller_number: Optional[str]
    called_number: Optional[str]
    caller_name: Optional[str]
    context_name: Optional[str]
    session_store: Any          # SessionStore
    ari_client: Any             # ARIClient - NEEDS REPLACEMENT with ESL client
    config: Any
    provider_name: str
    provider_session: Any
    
    async def get_session(self): ...
    async def update_session(self, **kwargs): ...
    def get_config_value(self, key: str, default=None): ...  # dot-notation config access
```

### HangupCallTool:
```python
async def execute(self, parameters, context):
    farewell = parameters.get('farewell_message') or config_default
    await context.update_session(cleanup_after_tts=True)  # Engine hangs up after TTS
    return {"status": "success", "message": farewell, "will_hangup": True}
```

### UnifiedTransferTool (blind_transfer):
Resolves destination from `tools.transfer.destinations` config. Types:
- `extension` -> `continue_in_dialplan(channel_id, context="from-internal", extension=target)`
- `queue` -> `continue_in_dialplan(channel_id, context="ext-queues", extension=target)`
- `ringgroup` -> `continue_in_dialplan(channel_id, context="ext-group", extension=target)`

For FreeSWITCH: use `uuid_transfer` to appropriate dialplan context.

### LiveAgentTransferTool:
Resolves live agent from `tools.extensions.internal` config (live_agent flag, name/alias match).
Falls back to `tools.transfer.destinations` with `live_agent: true`.
Delegates actual transfer to `UnifiedTransferTool`.

---

## 6. Configuration (`config/ai-agent.yaml`)

### Top-level keys:
```yaml
active_pipeline: null
asterisk:                    # -> REPLACE with freeswitch: section
  app_name: asterisk-ai-voice-agent
  # host, port, scheme, username, password loaded from .env
audio_transport: audiosocket  # "audiosocket" or "externalmedia"
audiosocket:
  format: slin               # slin (8kHz) or ulaw
  host: 127.0.0.1
  port: 8090
barge_in:
  enabled: true
  initial_protection_ms: 200
  min_ms: 250
  energy_threshold: 1000
  # ... many more barge-in tuning params
config_version: 6
farewell_hangup_delay_sec: 3
contexts:
  default:
    greeting: "Hello"
    profile: telephony_ulaw_8k
    prompt: "You are Asterisk, an AI Assistant..."
    provider: local
    tools: [hangup_call]
  # Many demo contexts with different providers
```

### Context config structure:
```yaml
contexts:
  <name>:
    greeting: str              # Initial greeting (supports {caller_name} placeholder)
    profile: str               # Audio profile name
    prompt: str                # System prompt for LLM
    provider: str              # Provider name (local, deepgram, openai_realtime, google_live, elevenlabs)
    pipeline: str              # Optional modular pipeline name
    tools: [str]               # Allowed in-call tools
    pre_call_tools: [str]      # Tools run before greeting
    post_call_tools: [str]     # Tools run after hangup
```

---

## 7. Config Models (`src/config.py`)

### AsteriskConfig (to be replaced with FreeSWITCHConfig):
```python
class AsteriskConfig(BaseModel):
    host: str
    port: int = 8088
    scheme: str = "http"        # http/https
    ssl_verify: bool = True
    username: str
    password: str
    app_name: str = "ai-voice-agent"
```

### AudioSocketConfig:
```python
class AudioSocketConfig(BaseModel):
    host: str = "127.0.0.1"
    advertise_host: Optional[str] = None
    port: int = 8090
    format: str = "ulaw"       # 'ulaw' or 'slin16'
```

### ExternalMediaConfig:
```python
class ExternalMediaConfig(BaseModel):
    rtp_host: str = "127.0.0.1"
    advertise_host: Optional[str] = None
    rtp_port: int = 18080
    codec: str = "ulaw"
    direction: str = "both"
    format: str = "slin16"     # Engine internal format
    sample_rate: Optional[int] = None
```

### Provider configs: `LocalProviderConfig`, `DeepgramProviderConfig`, `OpenAIProviderConfig`, `GoogleProviderConfig`

---

## 8. Docker / Deployment

### docker-compose.yml:
- All services use `network_mode: host`
- `ai_engine`: Python app, mounts src/config/models/data, runs as `appuser` in `asterisk` group
- `local_ai_server`: Local AI models (Vosk, Sherpa, Kokoro, Piper, llama.cpp)
- `admin_ui`: FastAPI admin panel, mounts Docker socket for container management

### Dockerfile (ai_engine):
- Python 3.11-slim-bookworm, two-stage build
- Installs from requirements.txt into venv
- Runs as non-root `appuser`, member of `asterisk` group (GID configurable)

### Key dependencies (requirements.txt):
```
websockets==15.0.1      # ARI WS client -> REPLACE with ESL library
aiohttp==3.13.3         # ARI HTTP client -> may still need for admin API
pydantic>=2.7.0         # Config validation
structlog==25.5.0       # Structured logging
prometheus-client==0.24.1
numpy>=1.24.0           # Audio resampling
webrtcvad==2.0.10       # Voice activity detection
tenacity==8.2.3         # Retry logic
openai>=1.0.0           # Summary generation
PyYAML==6.0.3
```

---

## 9. Key Interfaces for Porting

### What MUST change:
1. **`ARIClient`** -> `ESLClient` (FreeSWITCH Event Socket Library)
   - WS event subscription -> ESL `event plain ALL` or selective
   - HTTP commands -> ESL `api` and `bgapi` commands
   - Channel origination -> `originate` ESL command
   - Bridge management -> Conference API or `uuid_bridge`

2. **Event names** in `Engine.__init__`:
   - StasisStart -> CHANNEL_ANSWER + custom app logic
   - StasisEnd -> CHANNEL_HANGUP_COMPLETE
   - ChannelDestroyed -> CHANNEL_DESTROY
   - ChannelDtmfReceived -> DTMF
   - ChannelVarset -> not directly mapped (read vars on demand)

3. **Dialplan integration**:
   - Asterisk: `Stasis(app_name)` routes to ARI app
   - FreeSWITCH: Custom app via `socket` or `mod_esl`, or `mod_audio_fork`
   
4. **Transfer tools**: 
   - `continue_in_dialplan()` -> `uuid_transfer` to FreeSWITCH dialplan contexts
   - Context names: `from-internal`, `ext-queues`, `ext-group` are FreePBX-specific
   - FusionPBX uses different context naming

5. **Audio transport**:
   - AudioSocket: FreeSWITCH has no native AudioSocket, but options include:
     - `mod_audio_fork` (WebSocket-based audio streaming)
     - Custom `mod_esl` with media bug
     - The AudioSocketServer TLV protocol could be kept if a FS module speaks it

### What can stay mostly unchanged:
- All provider integrations (Deepgram, OpenAI, Google, Local)
- Tool framework (base classes, tool definitions, execution context)
- Audio processing (VAD, resampling, streaming playback)
- Session management (SessionStore, CallSession)
- Config system (except AsteriskConfig -> FreeSWITCHConfig)
- Admin UI (with adapted backend endpoints)
- Pipeline orchestrator
