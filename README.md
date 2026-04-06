# AVA AI Voice Agent for FusionPBX

![Version](https://img.shields.io/badge/version-6.4.0--fusionpbx-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Docker](https://img.shields.io/badge/docker-compose-blue.svg)
![FreeSWITCH](https://img.shields.io/badge/FreeSWITCH-1.10+-orange.svg)

AI-powered voice agent for FusionPBX/FreeSWITCH. Forked from [AVA AI Voice Agent for Asterisk](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk) by hkjarral, ported to use FreeSWITCH ESL (Event Socket Library) and AudioSocket instead of Asterisk ARI.

---

## Prerequisites

- **FusionPBX** with FreeSWITCH, modules enabled:
  - `mod_event_socket` (ESL inbound/outbound)
  - `mod_audiosocket` (bidirectional audio streaming)
- **Docker** and **Docker Compose**
- AI provider credentials (Deepgram, OpenAI, etc.) in `.env`

---

## Quick Start

```bash
cp .env.example .env
# Edit .env — set ESL credentials and AI provider keys
docker compose up -d
```

The engine listens for:
- **Inbound ESL** on port `8021` (FreeSWITCH connects to AVA)
- **Outbound ESL** on port `8084` (AVA connects to FreeSWITCH for outbound calls)
- **AudioSocket** on port `8090` (bidirectional audio)

---

## Configuration

### ESL Settings (`.env`)

```env
ESL_HOST=your-freeswitch-host
ESL_PORT=8021
ESL_PASSWORD=ClueCon

AUDIOSOCKET_HOST=0.0.0.0
AUDIOSOCKET_PORT=8090
```

### Tenant Configs

Per-tenant configuration lives in `config/tenants/`. Each file defines AI provider, tools, and call behavior for one domain.

### FreeSWITCH Dialplan

Sample dialplan XML is in `freeswitch/dialplan/`. Deploy to your FusionPBX dialplan to route calls to AVA via AudioSocket:

```xml
<action application="audiosocket" data="your-ava-host:8090"/>
```

---

## Architecture

```
FreeSWITCH  ←──ESL outbound──→  AVA ESL Outbound Server  (inbound calls)
FreeSWITCH  ←──ESL inbound───→  AVA ESL Inbound Client   (outbound calls)
FreeSWITCH  ←──AudioSocket───→  AVA AudioSocket Server   (audio stream)
```

- **Outbound ESL server** (`src/esl_outbound_server.py`): FreeSWITCH connects here when a call arrives. AVA handles the session and routes audio via AudioSocket.
- **Inbound ESL client** (`src/esl_client.py`): AVA connects to FreeSWITCH ESL to originate outbound calls or send commands.
- **AudioSocket** (`src/audio/audiosocket_server.py`): Bidirectional raw audio between FreeSWITCH and the AI engine.
- **ARI compat shim** (`src/ari_compat.py`): Wraps ESL in an ARI-compatible interface so existing telephony tools work unchanged.

---

## Original Project

This fork is based on [hkjarral/AVA-AI-Voice-Agent-for-Asterisk](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk). See that repo for full feature documentation, pipeline options, and provider configuration details.
