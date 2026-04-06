# AVA AI Voice Agent for FusionPBX — Design Spec

## Overview

Fork of [AVA-AI-Voice-Agent-for-Asterisk](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk) adapted to work with FusionPBX (FreeSWITCH) instead of Asterisk. Replaces the ARI integration layer with FreeSWITCH ESL while keeping all AI provider pipelines, audio processing, and admin UI intact.

## Decisions

| Decision | Choice |
|---|---|
| Approach | Fork & Replace (reuse ~70% of AVA code) |
| PBX integration | FreeSWITCH ESL (inbound + outbound) |
| Audio transport | AudioSocket TCP (mod_audiosocket) |
| Multi-tenant | Single ai_engine instance, domain from channel variable |
| Call direction | Inbound + Outbound |
| AI providers | All AVA providers + LiteLLM/Claude |
| Deploy target | aicore (93.189.136.90), FreeSWITCH on voip6 |
| Admin UI | Standalone on port 3003 |

## Architecture

```
┌─────────────────────┐         ┌──────────────────────────────┐
│   voip6 (FusionPBX) │         │     aicore (93.189.136.90)   │
│                     │         │                              │
│  FreeSWITCH         │         │  ┌─────────────┐            │
│   ├─ mod_audiosocket│◄──TCP──►│  │  ai_engine   │            │
│   ├─ mod_event_socket│◄──TCP──►│  │  (Python)    │            │
│   │  (outbound ESL) │         │  │   ├─ esl_client.py (NEW) │
│   │                 │         │  │   ├─ engine.py (adapted)  │
│   └─ dialplan XML   │         │  │   └─ telephony tools      │
│      socket(host:port)        │  └──────┬───────┘            │
│                     │         │         │                    │
│                     │         │  ┌──────▼───────┐            │
│                     │         │  │ local_ai_srv  │            │
│                     │         │  │ STT/LLM/TTS   │            │
│                     │         │  └───────────────┘            │
│                     │         │                              │
│                     │         │  ┌───────────────┐            │
│                     │         │  │  admin_ui      │ :3003     │
│                     │         │  └───────────────┘            │
│                     │         │                              │
│                     │         │  Ollama / LiteLLM / Voxtral  │
└─────────────────────┘         └──────────────────────────────┘
```

## Component 1: ESL Client (`src/esl_client.py`)

Replaces `src/ari_client.py` (~1200 lines). Uses `greenswitch` library (async Python ESL).

### Inbound ESL Connection
- Persistent connection to voip6:8021
- Subscribes to global events (CHANNEL_CREATE, HEARTBEAT)
- Used for: originating outbound calls, executing commands on existing channels
- Authenticated with FusionPBX ESL password

### Outbound ESL Server
- TCP server on aicore:8085
- FreeSWITCH connects when dialplan executes `socket()` app
- One connection per call, lifecycle tied to channel
- Commands: `answer`, `playback`, `bridge`, `transfer`, `hangup`
- Reads channel variables: `variable_domain_name`, `variable_ai_provider`, `variable_ai_context`

### Event Mapping

| AVA (ARI) | New (ESL) |
|---|---|
| StasisStart | Outbound ESL connect |
| StasisEnd | CHANNEL_HANGUP_COMPLETE |
| ChannelDtmfReceived | DTMF |
| ChannelDestroyed | CHANNEL_DESTROY |
| ChannelTalkingStarted | DETECTED_SPEECH (if enabled) |

## Component 2: Engine Adaptation (`src/engine.py`)

The main engine (~4000+ lines) needs targeted changes:

- Replace all `ari_client` calls with `esl_client` equivalents
- Replace ARI event handlers with ESL event handlers
- StasisStart handler → outbound ESL connection handler
- Channel variable reading: `ari_client.get_variable()` → ESL `getVariable()`
- Audio initiation: instead of ARI ExternalMedia, use `audiosocket` dialplan app via ESL

Unchanged: VAD, barge-in, streaming playback, session management, provider routing, audio resampling.

## Component 3: Telephony Tools (`src/tools/telephony/`)

| Tool | ARI (current) | ESL (new) |
|---|---|---|
| answer_call | `POST /channels/{id}/answer` | `answer` (outbound ESL) |
| hangup_call | `DELETE /channels/{id}` | `uuid_kill {uuid}` / `hangup` |
| transfer_call | `POST /channels/{id}/continue` | `uuid_transfer {uuid} {dest} XML {domain}` |
| blind_transfer | bridge + redirect | `uuid_transfer` |
| attended_transfer | originate + bridge | `originate` + `uuid_bridge` |
| play_audio | `POST /bridges/{id}/play` | `uuid_broadcast {uuid} {path}` / `playback` |
| send_dtmf | `POST /channels/{id}/dtmf` | `uuid_send_dtmf {uuid} {digits}` |
| park_call | bridge to parking | `uuid_transfer` to FusionPBX parking lot |
| voicemail | redirect to VM context | `uuid_transfer` to `*99{ext} XML {domain}` |

## Component 4: Dialplan (FreeSWITCH/FusionPBX)

XML snippet added via FusionPBX dialplan manager or directly:

```xml
<extension name="ai-agent">
  <condition field="destination_number" expression="^(ai-agent|5900)$">
    <action application="set" data="ai_provider=litellm_hybrid"/>
    <action application="set" data="ai_context=receptionist"/>
    <action application="socket" data="93.189.136.90:8085 async full"/>
  </condition>
</extension>
```

Requires `mod_audiosocket` installed on voip6.

## Component 5: Multi-tenant Configuration

### Config Structure
```
config/
├── ai-agent.yaml          # global config (ports, ESL, provider defaults)
├── ai-agent.local.yaml    # local overrides (secrets, git-ignored)
└── tenants/
    ├── db.voip.yaml
    ├── norcia.voip6.yaml
    └── default.yaml        # fallback
```

### Tenant Resolution
1. Outbound ESL receives connection → reads `variable_domain_name`
2. Looks up `config/tenants/{domain}.yaml`
3. If not found → uses `default.yaml`
4. Merge order: `default.yaml` ← `tenant.yaml` ← channel variables

### Per-tenant Settings
- AI provider (LLM, STT, TTS)
- System prompt / context
- Language and TTS voice
- Transfer extensions mapping
- Business hours (outside hours → voicemail)
- Custom greeting audio

## Component 6: Outbound Calls

Via inbound ESL connection to voip6:8021:

```
originate {domain_name=db.voip,ai_context=appointment}sofia/gateway/trunk/+39xxx &socket('93.189.136.90:8085 async full')
```

The originated call connects back to ai_engine via outbound ESL, same flow as inbound.

## Component 7: Docker Compose

```yaml
services:
  ai_engine:
    build: ./ai_engine
    network_mode: host
    volumes:
      - ./config:/app/config
      - ./models:/app/models
    environment:
      - ESL_HOST=${ESL_HOST}
      - ESL_PORT=8021
      - ESL_PASSWORD=${ESL_PASSWORD}
      - AUDIOSOCKET_PORT=8090
      - OUTBOUND_ESL_PORT=8085

  local_ai_server:
    build: ./local_ai_server
    network_mode: host
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  admin_ui:
    build: ./admin_ui
    ports:
      - "3003:3003"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

## What Stays Unchanged

- All AI provider integrations (`src/providers/`, `src/pipelines/`)
- AudioSocket server (`src/audio/audiosocket_server.py`) — same TLV protocol
- Local AI server (Vosk, Whisper, Piper, Kokoro, Ollama backends)
- Audio resampling, VAD, streaming playback manager
- Session store, call history, conversation coordinator
- Transport orchestrator (audio format negotiation)
- HTTP tools, email tools, MCP servers
- Admin UI (except Asterisk-specific config panels)

## What Gets Removed

- `src/ari_client.py` — replaced by `src/esl_client.py`
- `src/engine_external_media.py` — ExternalMedia is ARI-specific
- `src/rtp_server.py` — not needed with AudioSocket
- Asterisk dialplan configs
- Any Asterisk-specific references in admin UI

## Dependencies

New Python dependencies:
- `greenswitch` — async ESL library

Removed:
- No ARI-specific libraries needed

FreeSWITCH modules required on voip6:
- `mod_event_socket` (already enabled in FusionPBX)
- `mod_audiosocket` (may need installation)
