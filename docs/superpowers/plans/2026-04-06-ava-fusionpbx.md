# AVA AI Voice Agent for FusionPBX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port AVA AI Voice Agent from Asterisk/ARI to FusionPBX/FreeSWITCH by replacing the ARI client with an ESL client, adapting event handlers and telephony tools, and adding multi-tenant support.

**Architecture:** Fork of AVA with a new `ESLClient` replacing `ARIClient`. Outbound ESL server (TCP :8085) handles inbound calls from FreeSWITCH dialplan `socket()` app. Inbound ESL connection to voip6:8021 handles outbound call origination and global commands. AudioSocket (:8090) reused as-is for bidirectional audio. Multi-tenant config resolved from `domain_name` channel variable.

**Tech Stack:** Python 3.11, greenswitch (async ESL), asyncio, FreeSWITCH mod_event_socket + mod_audiosocket, Docker

**Source reference:** `docs/ava-source-reference.md` contains full API signatures from the original AVA repo.

---

## File Structure

### New files:
- `src/esl_client.py` — FreeSWITCH ESL client (replaces `src/ari_client.py`)
- `src/esl_outbound_server.py` — Outbound ESL TCP server for inbound call handling
- `src/config_freeswitch.py` — FreeSWITCH + tenant config models
- `config/tenants/default.yaml` — Default tenant config
- `config/tenants/db.voip.yaml` — Example tenant
- `freeswitch/dialplan/ai-agent.xml` — FreeSWITCH dialplan snippet
- `tests/test_esl_client.py`
- `tests/test_esl_outbound_server.py`
- `tests/test_telephony_tools.py`
- `tests/test_tenant_config.py`
- `tests/conftest.py` — Shared fixtures (mock ESL, mock sessions)

### Modified files:
- `src/engine.py` — Replace ARI event handlers with ESL, remove bridge logic
- `src/tools/context.py` — `ari_client` field → `esl_client`
- `src/tools/telephony/hangup.py` — Use ESL commands
- `src/tools/telephony/unified_transfer.py` — FusionPBX dialplan contexts
- `src/tools/telephony/live_agent_transfer.py` — ESL transfer
- `src/tools/telephony/attended_transfer.py` — ESL originate + bridge
- `src/tools/telephony/voicemail.py` — FusionPBX voicemail path
- `src/tools/telephony/check_extension_status.py` — ESL show registrations
- `src/config.py` — Replace `AsteriskConfig` with `FreeSWITCHConfig`
- `config/ai-agent.yaml` — Replace `asterisk:` section with `freeswitch:` + `tenants:`
- `docker-compose.yml` — Update env vars, remove Asterisk refs
- `Dockerfile` — Remove asterisk group, add greenswitch dep
- `requirements.txt` — Replace websockets with greenswitch
- `.env.example` — FreeSWITCH ESL credentials

### Removed files:
- `src/ari_client.py` — Replaced by `esl_client.py`
- `src/engine_external_media.py` — ARI ExternalMedia not needed
- `src/rtp_server.py` — Not needed with AudioSocket

---

### Task 1: Project Setup — Clone AVA and Prepare Repo

**Files:**
- Create: `requirements.txt` (modified from AVA)
- Create: `.env.example`
- Create: `tests/conftest.py`
- Remove: `src/ari_client.py`, `src/engine_external_media.py`, `src/rtp_server.py`

- [ ] **Step 1: Clone AVA repo into project**

```bash
cd /Users/daniele/Desktop/AVA-FusionPBX
git remote add ava https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk.git
git fetch ava main
git checkout -b main ava/main
```

- [ ] **Step 2: Create feature branch**

```bash
git checkout -b feature/fusionpbx-port
```

- [ ] **Step 3: Update requirements.txt — replace ARI deps with ESL**

Replace `websockets==15.0.1` with `greenswitch>=0.4.0`. Keep `aiohttp` (used by admin API and providers). Add `pytest>=7.0`, `pytest-asyncio>=0.21`.

In `requirements.txt`, change:
```
# Remove:
# websockets==15.0.1  (was for ARI WebSocket)

# Add:
greenswitch>=0.4.0
pytest>=7.0
pytest-asyncio>=0.21
```

- [ ] **Step 4: Create `.env.example`**

```bash
cat > .env.example << 'ENVEOF'
# FreeSWITCH ESL
ESL_HOST=voip6-ip-here
ESL_PORT=8021
ESL_PASSWORD=ClueCon

# Outbound ESL Server (ai_engine listens for FS connections)
OUTBOUND_ESL_HOST=0.0.0.0
OUTBOUND_ESL_PORT=8085

# AudioSocket
AUDIOSOCKET_HOST=0.0.0.0
AUDIOSOCKET_PORT=8090

# AI Providers (keep existing)
OPENAI_API_KEY=
DEEPGRAM_API_KEY=
ELEVENLABS_API_KEY=
GOOGLE_API_KEY=

# LiteLLM (Tecnoadsl stack)
LITELLM_BASE_URL=http://localhost:4001
LITELLM_API_KEY=
ENVEOF
```

- [ ] **Step 5: Remove Asterisk-specific files**

```bash
git rm src/ari_client.py src/engine_external_media.py src/rtp_server.py
```

- [ ] **Step 6: Create test conftest with shared fixtures**

Write `tests/conftest.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass, field
from typing import Dict, Optional, Any


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class MockESLClient:
    """Mock ESL client for testing telephony tools and engine logic."""

    def __init__(self):
        self.commands_sent = []
        self.events_subscribed = []
        self.connected = True
        self.channel_vars: Dict[str, Dict[str, str]] = {}

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send_command(self, command: str) -> dict:
        self.commands_sent.append(command)
        return {"Reply-Text": "+OK", "Content-Type": "command/reply"}

    async def api(self, command: str) -> str:
        self.commands_sent.append(f"api {command}")
        return "+OK"

    async def bgapi(self, command: str) -> str:
        self.commands_sent.append(f"bgapi {command}")
        return "+OK Job-UUID: test-job-uuid"

    async def answer(self, conn):
        self.commands_sent.append("answer")

    async def hangup(self, conn=None, uuid=None, cause="NORMAL_CLEARING"):
        cmd = f"hangup {uuid or 'conn'} {cause}"
        self.commands_sent.append(cmd)

    async def transfer(self, uuid: str, destination: str, dialplan: str = "XML",
                       context: str = "default"):
        cmd = f"uuid_transfer {uuid} {destination} {dialplan} {context}"
        self.commands_sent.append(cmd)

    async def bridge(self, uuid1: str, uuid2: str):
        cmd = f"uuid_bridge {uuid1} {uuid2}"
        self.commands_sent.append(cmd)

    async def originate(self, url: str, extension: str, dialplan: str = "XML",
                        context: str = "default", cid_name: str = "", cid_num: str = "",
                        timeout: int = 30, channel_vars: dict = None) -> str:
        var_str = ",".join(f"{k}={v}" for k, v in (channel_vars or {}).items())
        cmd = f"originate {{{var_str}}}{url} {extension} {dialplan} {context} '{cid_name}' {cid_num} {timeout}"
        self.commands_sent.append(cmd)
        return "test-originated-uuid"

    async def playback(self, conn=None, uuid=None, path: str = ""):
        cmd = f"playback {uuid or 'conn'} {path}"
        self.commands_sent.append(cmd)

    async def get_channel_var(self, uuid: str, var_name: str) -> Optional[str]:
        return self.channel_vars.get(uuid, {}).get(var_name)

    async def set_channel_var(self, uuid: str, var_name: str, value: str):
        if uuid not in self.channel_vars:
            self.channel_vars[uuid] = {}
        self.channel_vars[uuid][var_name] = value

    async def broadcast(self, uuid: str, path: str, leg: str = "aleg"):
        cmd = f"uuid_broadcast {uuid} {path} {leg}"
        self.commands_sent.append(cmd)

    async def kill(self, uuid: str, cause: str = "NORMAL_CLEARING"):
        cmd = f"uuid_kill {uuid} {cause}"
        self.commands_sent.append(cmd)

    def last_command(self) -> str:
        return self.commands_sent[-1] if self.commands_sent else ""


@pytest.fixture
def mock_esl():
    return MockESLClient()


@dataclass
class MockSession:
    call_id: str = "test-call-id"
    caller_channel_id: str = "test-channel-uuid"
    bridge_id: Optional[str] = None
    caller_number: str = "+391234567890"
    called_number: str = "5900"
    caller_name: str = "Test Caller"
    domain_name: str = "db.voip"
    provider_name: str = "litellm_hybrid"
    cleanup_after_tts: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def mock_session():
    return MockSession()


@dataclass
class MockToolContext:
    call_id: str = "test-call-id"
    caller_channel_id: str = "test-channel-uuid"
    bridge_id: Optional[str] = None
    caller_number: str = "+391234567890"
    called_number: str = "5900"
    caller_name: str = "Test Caller"
    context_name: str = "default"
    domain_name: str = "db.voip"
    esl_client: Any = None
    config: Any = None
    provider_name: str = "litellm_hybrid"
    provider_session: Any = None
    session_store: Any = None
    _session: Any = None

    async def get_session(self):
        return self._session

    async def update_session(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self._session, k):
                setattr(self._session, k, v)
            else:
                self._session.extra[k] = v

    def get_config_value(self, key, default=None):
        return default


@pytest.fixture
def mock_tool_context(mock_esl, mock_session):
    ctx = MockToolContext()
    ctx.esl_client = mock_esl
    ctx._session = mock_session
    return ctx
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: prepare repo for FusionPBX port

Remove Asterisk-specific files (ari_client, external_media, rtp_server).
Add greenswitch ESL dependency, test fixtures, env template."
```

---

### Task 2: FreeSWITCH Config Models

**Files:**
- Create: `src/config_freeswitch.py`
- Modify: `src/config.py`
- Create: `tests/test_tenant_config.py`
- Create: `config/tenants/default.yaml`
- Create: `config/tenants/db.voip.yaml`

- [ ] **Step 1: Write failing test for FreeSWITCH config**

Write `tests/test_tenant_config.py`:

```python
import pytest
from src.config_freeswitch import FreeSWITCHConfig, TenantConfig, TenantResolver


def test_freeswitch_config_defaults():
    config = FreeSWITCHConfig()
    assert config.esl_host == "127.0.0.1"
    assert config.esl_port == 8021
    assert config.esl_password == "ClueCon"
    assert config.outbound_host == "0.0.0.0"
    assert config.outbound_port == 8085


def test_freeswitch_config_from_env(monkeypatch):
    monkeypatch.setenv("ESL_HOST", "10.0.0.1")
    monkeypatch.setenv("ESL_PORT", "8021")
    monkeypatch.setenv("ESL_PASSWORD", "secret")
    config = FreeSWITCHConfig.from_env()
    assert config.esl_host == "10.0.0.1"
    assert config.esl_password == "secret"


def test_tenant_config_loads():
    tenant = TenantConfig(
        domain="db.voip",
        provider="litellm_hybrid",
        language="it",
        tts_voice="it_male",
        context_prompt="Sei l'assistente di Tecnoadsl",
        transfer_extensions={"sales": "1001", "support": "1002"},
    )
    assert tenant.domain == "db.voip"
    assert tenant.transfer_extensions["sales"] == "1001"


def test_tenant_resolver_finds_tenant(tmp_path):
    # Create tenant yaml
    tenant_dir = tmp_path / "tenants"
    tenant_dir.mkdir()
    (tenant_dir / "db.voip.yaml").write_text(
        "domain: db.voip\nprovider: litellm_hybrid\nlanguage: it\n"
        "tts_voice: it_male\ncontext_prompt: Test\n"
    )
    (tenant_dir / "default.yaml").write_text(
        "domain: default\nprovider: local\nlanguage: en\n"
        "tts_voice: en_male\ncontext_prompt: Default assistant\n"
    )

    resolver = TenantResolver(str(tenant_dir))
    tenant = resolver.resolve("db.voip")
    assert tenant.domain == "db.voip"
    assert tenant.provider == "litellm_hybrid"


def test_tenant_resolver_falls_back_to_default(tmp_path):
    tenant_dir = tmp_path / "tenants"
    tenant_dir.mkdir()
    (tenant_dir / "default.yaml").write_text(
        "domain: default\nprovider: local\nlanguage: en\n"
        "tts_voice: en_male\ncontext_prompt: Default assistant\n"
    )

    resolver = TenantResolver(str(tenant_dir))
    tenant = resolver.resolve("unknown.domain")
    assert tenant.domain == "default"
    assert tenant.provider == "local"


def test_tenant_resolver_merges_channel_vars(tmp_path):
    tenant_dir = tmp_path / "tenants"
    tenant_dir.mkdir()
    (tenant_dir / "default.yaml").write_text(
        "domain: default\nprovider: local\nlanguage: en\n"
        "tts_voice: en_male\ncontext_prompt: Default\n"
    )

    resolver = TenantResolver(str(tenant_dir))
    tenant = resolver.resolve("default", overrides={"provider": "openai_realtime"})
    assert tenant.provider == "openai_realtime"
    assert tenant.language == "en"  # not overridden
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/daniele/Desktop/AVA-FusionPBX
python -m pytest tests/test_tenant_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.config_freeswitch'`

- [ ] **Step 3: Implement FreeSWITCH config models**

Write `src/config_freeswitch.py`:

```python
"""FreeSWITCH and multi-tenant configuration models."""

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, Optional

import yaml


@dataclass
class FreeSWITCHConfig:
    """FreeSWITCH ESL connection settings."""
    esl_host: str = "127.0.0.1"
    esl_port: int = 8021
    esl_password: str = "ClueCon"
    outbound_host: str = "0.0.0.0"
    outbound_port: int = 8085

    @classmethod
    def from_env(cls) -> "FreeSWITCHConfig":
        return cls(
            esl_host=os.environ.get("ESL_HOST", "127.0.0.1"),
            esl_port=int(os.environ.get("ESL_PORT", "8021")),
            esl_password=os.environ.get("ESL_PASSWORD", "ClueCon"),
            outbound_host=os.environ.get("OUTBOUND_ESL_HOST", "0.0.0.0"),
            outbound_port=int(os.environ.get("OUTBOUND_ESL_PORT", "8085")),
        )


@dataclass
class TenantConfig:
    """Per-domain tenant configuration."""
    domain: str = "default"
    provider: str = "local"
    language: str = "en"
    tts_voice: str = "en_male"
    context_prompt: str = "You are an AI assistant."
    greeting: str = "Hello, how can I help you?"
    transfer_extensions: Dict[str, str] = field(default_factory=dict)
    business_hours: Optional[Dict] = None
    voicemail_extension: str = "*99"


class TenantResolver:
    """Resolves tenant config from domain name with fallback to default."""

    def __init__(self, tenant_dir: str):
        self._tenant_dir = Path(tenant_dir)
        self._cache: Dict[str, TenantConfig] = {}

    def resolve(self, domain: str, overrides: Optional[Dict] = None) -> TenantConfig:
        tenant = self._load(domain)
        if tenant is None:
            tenant = self._load("default")
        if tenant is None:
            tenant = TenantConfig()

        if overrides:
            valid_fields = {f.name for f in fields(TenantConfig)}
            for key, value in overrides.items():
                if key in valid_fields and value is not None:
                    setattr(tenant, key, value)

        return tenant

    def _load(self, domain: str) -> Optional[TenantConfig]:
        if domain in self._cache:
            return TenantConfig(**self._cache[domain].__dict__)

        path = self._tenant_dir / f"{domain}.yaml"
        if not path.exists():
            return None

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        valid_fields = {f.name for f in fields(TenantConfig)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        tenant = TenantConfig(**filtered)
        self._cache[domain] = tenant
        return TenantConfig(**tenant.__dict__)

    def reload(self):
        self._cache.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_tenant_config.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Create tenant config files**

Write `config/tenants/default.yaml`:

```yaml
domain: default
provider: local
language: en
tts_voice: en_male
context_prompt: >
  You are an AI phone assistant. Be helpful and concise.
greeting: "Hello, how can I help you?"
transfer_extensions: {}
voicemail_extension: "*99"
```

Write `config/tenants/db.voip.yaml`:

```yaml
domain: db.voip
provider: litellm_hybrid
language: it
tts_voice: it_male
context_prompt: >
  Sei l'assistente telefonico di Tecnoadsl. Rispondi in italiano.
  Puoi trasferire le chiamate al reparto vendite o assistenza tecnica.
greeting: "Buongiorno, sono l'assistente di Tecnoadsl. Come posso aiutarla?"
transfer_extensions:
  vendite: "1001"
  assistenza: "1002"
  amministrazione: "1003"
voicemail_extension: "*99"
```

- [ ] **Step 6: Update `src/config.py` — replace AsteriskConfig**

In `src/config.py`, replace:
```python
class AsteriskConfig(BaseModel):
    host: str
    port: int = 8088
    scheme: str = "http"
    ssl_verify: bool = True
    username: str
    password: str
    app_name: str = "ai-voice-agent"
```

With import + alias:
```python
from src.config_freeswitch import FreeSWITCHConfig

# FreeSWITCHConfig replaces AsteriskConfig — see src/config_freeswitch.py
```

Also update any references to `self.config.asterisk` → `self.config.freeswitch` in the config loading logic.

- [ ] **Step 7: Commit**

```bash
git add src/config_freeswitch.py tests/test_tenant_config.py config/tenants/ src/config.py
git commit -m "feat: add FreeSWITCH config models and multi-tenant resolver

TenantResolver loads per-domain YAML configs with fallback to default.
FreeSWITCHConfig replaces AsteriskConfig with ESL connection settings."
```

---

### Task 3: ESL Inbound Client

**Files:**
- Create: `src/esl_client.py`
- Create: `tests/test_esl_client.py`

- [ ] **Step 1: Write failing test for ESL client**

Write `tests/test_esl_client.py`:

```python
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

    event = {
        "Event-Name": "DTMF",
        "Unique-ID": "test-uuid",
        "DTMF-Digit": "5",
    }
    await client._dispatch_event(event)
    handler.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_esl_client_api_formats_command():
    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    # Mock the internal connection
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
        extension="5900",
        context="default",
        dialplan="XML",
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_esl_client.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.esl_client'`

- [ ] **Step 3: Implement ESL inbound client**

Write `src/esl_client.py`:

```python
"""FreeSWITCH ESL Inbound Client — replaces ARIClient for PBX control."""

import asyncio
import logging
from typing import Any, Callable, Awaitable, Dict, List, Optional

import greenswitch

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
        self._conn: Optional[greenswitch.InboundESL] = None
        self.event_handlers: Dict[str, List[Callable]] = {}
        self._listener_task: Optional[asyncio.Task] = None

    async def connect(self):
        """Connect to FreeSWITCH ESL."""
        self._conn = greenswitch.InboundESL(
            host=self.host, port=self.port, password=self.password
        )
        await self._conn.connect()
        self.connected = True
        logger.info("ESL inbound connected to %s:%d", self.host, self.port)

        # Subscribe to all events
        await self._conn.send("events plain ALL")

    async def disconnect(self):
        """Disconnect from FreeSWITCH ESL."""
        if self._listener_task:
            self._listener_task.cancel()
        if self._conn:
            self._conn.stop()
        self.connected = False
        logger.info("ESL inbound disconnected")

    def on_event(self, event_name: str, handler: Callable[[Dict], Awaitable[None]]):
        """Register an event handler."""
        if event_name not in self.event_handlers:
            self.event_handlers[event_name] = []
        self.event_handlers[event_name].append(handler)

    async def start_listening(self):
        """Start event listening loop with auto-reconnect."""
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
        """Dispatch event to registered handlers."""
        event_name = event.get("Event-Name")
        if event_name and event_name in self.event_handlers:
            for handler in self.event_handlers[event_name]:
                try:
                    await handler(event)
                except Exception as e:
                    logger.error("Event handler error for %s: %s", event_name, e)

    # --- FreeSWITCH API Commands ---

    async def api(self, command: str) -> str:
        """Execute synchronous ESL API command."""
        if not self._conn or not self.connected:
            raise ConnectionError("ESL not connected")
        result = await self._conn.api(command)
        return result.body if hasattr(result, "body") else str(result)

    async def bgapi(self, command: str) -> str:
        """Execute background ESL API command."""
        if not self._conn or not self.connected:
            raise ConnectionError("ESL not connected")
        result = await self._conn.bgapi(command)
        return result.body if hasattr(result, "body") else str(result)

    # --- Channel Commands ---

    async def kill(self, uuid: str, cause: str = "NORMAL_CLEARING") -> str:
        """Hang up a channel by UUID."""
        return await self.api(f"uuid_kill {uuid} {cause}")

    async def transfer(self, uuid: str, destination: str,
                       dialplan: str = "XML", context: str = "default") -> str:
        """Transfer a channel to a new destination."""
        return await self.api(f"uuid_transfer {uuid} {destination} {dialplan} {context}")

    async def bridge(self, uuid1: str, uuid2: str) -> str:
        """Bridge two channels together."""
        return await self.api(f"uuid_bridge {uuid1} {uuid2}")

    async def broadcast(self, uuid: str, path: str, leg: str = "aleg") -> str:
        """Broadcast audio to a channel."""
        return await self.api(f"uuid_broadcast {uuid} {path} {leg}")

    async def send_dtmf(self, uuid: str, digits: str) -> str:
        """Send DTMF digits to a channel."""
        return await self.api(f"uuid_send_dtmf {uuid} {digits}")

    async def get_channel_var(self, uuid: str, var_name: str) -> Optional[str]:
        """Get a channel variable by UUID."""
        result = await self.api(f"uuid_getvar {uuid} {var_name}")
        if result and not result.startswith("-ERR"):
            return result.strip()
        return None

    async def set_channel_var(self, uuid: str, var_name: str, value: str) -> str:
        """Set a channel variable by UUID."""
        return await self.api(f"uuid_setvar {uuid} {var_name} {value}")

    async def originate(self, url: str, extension: str, context: str = "default",
                        dialplan: str = "XML", cid_name: str = "", cid_num: str = "",
                        timeout: int = 30, channel_vars: Optional[Dict[str, str]] = None) -> str:
        """Originate an outbound call.

        Args:
            url: Channel URL (e.g., sofia/gateway/trunk/+39xxx)
            extension: Destination extension after answer
            context: Dialplan context
            dialplan: Dialplan type (XML)
            cid_name: Caller ID name
            cid_num: Caller ID number
            timeout: Ring timeout in seconds
            channel_vars: Channel variables to set on the originated channel
        """
        var_str = ""
        if channel_vars:
            pairs = ",".join(f"{k}={v}" for k, v in channel_vars.items())
            var_str = f"{{{pairs}}}"

        cmd = (
            f"originate {var_str}{url} {extension} {dialplan} {context} "
            f"'{cid_name}' {cid_num} {timeout}"
        )
        return await self.api(cmd)

    async def show_registrations(self, profile: str = "internal") -> str:
        """Show SIP registrations (for extension status check)."""
        return await self.api(f"sofia status profile {profile} reg")

    async def conference_list(self) -> str:
        """List active conferences."""
        return await self.api("conference list")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_esl_client.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/esl_client.py tests/test_esl_client.py
git commit -m "feat: add ESL inbound client for FreeSWITCH

Async client using greenswitch for persistent ESL connection.
Supports uuid_* commands, originate, event subscription."
```

---

### Task 4: Outbound ESL Server

**Files:**
- Create: `src/esl_outbound_server.py`
- Create: `tests/test_esl_outbound_server.py`

- [ ] **Step 1: Write failing test**

Write `tests/test_esl_outbound_server.py`:

```python
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

    # Simulate getVariable responses
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
    assert "audiosocket" in args[0].lower() or "playback" in args[0].lower()


@pytest.mark.asyncio
async def test_outbound_call_hangup():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock(body="+OK"))

    call = OutboundCall(conn=mock_conn, get_variable=AsyncMock(return_value=None))
    call.uuid = "test-uuid"

    await call.hangup()
    mock_conn.execute.assert_called_with("hangup", "NORMAL_CLEARING")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_esl_outbound_server.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement outbound ESL server**

Write `src/esl_outbound_server.py`:

```python
"""Outbound ESL Server — handles inbound calls from FreeSWITCH dialplan socket() app."""

import asyncio
import logging
from typing import Any, Callable, Awaitable, Dict, Optional

import greenswitch

logger = logging.getLogger(__name__)


class OutboundCall:
    """Represents a single inbound call received via outbound ESL.

    FreeSWITCH connects to us when the dialplan executes:
        <action application="socket" data="aicore:8085 async full"/>
    """

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
        """Read channel variables from the FreeSWITCH connection."""
        self.uuid = await self._get_variable("Unique-ID")
        self.domain_name = await self._get_variable("variable_domain_name")
        self.ai_provider = await self._get_variable("variable_ai_provider")
        self.ai_context = await self._get_variable("variable_ai_context")
        self.caller_number = await self._get_variable("Caller-Caller-ID-Number")
        self.caller_name = await self._get_variable("Caller-Caller-ID-Name")
        self.called_number = await self._get_variable("Caller-Destination-Number")

        logger.info(
            "Inbound call %s from %s to %s on domain %s (provider=%s, context=%s)",
            self.uuid, self.caller_number, self.called_number,
            self.domain_name, self.ai_provider, self.ai_context,
        )

    async def answer(self):
        """Answer the call."""
        await self.conn.execute("answer")
        logger.info("Answered call %s", self.uuid)

    async def hangup(self, cause: str = "NORMAL_CLEARING"):
        """Hang up the call."""
        await self.conn.execute("hangup", cause)
        logger.info("Hung up call %s (%s)", self.uuid, cause)

    async def start_audiosocket(self, host: str, port: int):
        """Start AudioSocket for bidirectional audio streaming.

        Executes the audiosocket dialplan app which connects TCP to our AudioSocket server.
        """
        audiosocket_uri = f"{host}:{port}"
        # Use uuid as AudioSocket UUID for correlation
        await self.conn.execute(
            "playback", f"audiosocket:{self.uuid}:{audiosocket_uri}"
        )
        logger.info("AudioSocket started for call %s -> %s", self.uuid, audiosocket_uri)

    async def playback(self, path: str):
        """Play an audio file."""
        await self.conn.execute("playback", path)

    async def execute(self, app: str, args: str = ""):
        """Execute an arbitrary FreeSWITCH dialplan application."""
        await self.conn.execute(app, args)

    async def set_variable(self, name: str, value: str):
        """Set a channel variable."""
        await self.conn.execute("set", f"{name}={value}")


class OutboundESLServer:
    """TCP server that accepts outbound ESL connections from FreeSWITCH.

    When FreeSWITCH executes `socket(host:port async full)` in the dialplan,
    it connects to this server. Each connection represents one call.
    """

    def __init__(self, host: str, port: int,
                 on_call: Callable[["OutboundCall"], Awaitable[None]]):
        self.host = host
        self.port = port
        self.on_call = on_call
        self._server = None
        self.active_calls: Dict[str, OutboundCall] = {}

    async def start(self):
        """Start listening for outbound ESL connections."""
        self._server = await greenswitch.OutboundESLServer.create(
            self._handle_connection,
            self.host,
            self.port,
        )
        logger.info("Outbound ESL server listening on %s:%d", self.host, self.port)

    async def _handle_connection(self, conn):
        """Handle a new outbound ESL connection (one per call)."""
        try:
            # Read the initial CHANNEL_DATA from FreeSWITCH
            async def get_variable(name):
                return conn.get_header(name)

            call = OutboundCall(conn=conn, get_variable=get_variable)
            await call.load_variables()

            if call.uuid:
                self.active_calls[call.uuid] = call

            # Delegate to the engine's call handler
            await self.on_call(call)
        except Exception as e:
            logger.error("Error handling outbound ESL connection: %s", e, exc_info=True)
        finally:
            if call.uuid and call.uuid in self.active_calls:
                del self.active_calls[call.uuid]

    async def stop(self):
        """Stop the outbound ESL server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Outbound ESL server stopped")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_esl_outbound_server.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/esl_outbound_server.py tests/test_esl_outbound_server.py
git commit -m "feat: add outbound ESL server for inbound call handling

TCP server accepts FreeSWITCH socket() connections.
OutboundCall reads channel vars and controls call lifecycle."
```

---

### Task 5: Telephony Tools — Port to ESL

**Files:**
- Modify: `src/tools/context.py`
- Modify: `src/tools/telephony/hangup.py`
- Modify: `src/tools/telephony/unified_transfer.py`
- Modify: `src/tools/telephony/live_agent_transfer.py`
- Modify: `src/tools/telephony/voicemail.py`
- Modify: `src/tools/telephony/check_extension_status.py`
- Create: `tests/test_telephony_tools.py`

- [ ] **Step 1: Write failing tests for telephony tools**

Write `tests/test_telephony_tools.py`:

```python
import pytest
from tests.conftest import MockESLClient, MockToolContext, MockSession


@pytest.mark.asyncio
async def test_hangup_tool_sends_farewell(mock_tool_context):
    from src.tools.telephony.hangup import HangupCallTool
    tool = HangupCallTool()
    result = await tool.execute(
        {"farewell_message": "Arrivederci!"},
        mock_tool_context,
    )
    assert result["status"] == "success"
    assert result["message"] == "Arrivederci!"
    assert result["will_hangup"] is True
    # Hangup is deferred (cleanup_after_tts), no direct ESL command
    session = await mock_tool_context.get_session()
    assert session.cleanup_after_tts is True


@pytest.mark.asyncio
async def test_blind_transfer_to_extension(mock_tool_context):
    from src.tools.telephony.unified_transfer import UnifiedTransferTool
    tool = UnifiedTransferTool()
    mock_tool_context.domain_name = "db.voip"

    result = await tool.execute(
        {"destination": "1001", "type": "extension"},
        mock_tool_context,
    )
    assert result["status"] == "success"
    esl = mock_tool_context.esl_client
    # Should have called uuid_transfer
    transfer_cmd = [c for c in esl.commands_sent if "uuid_transfer" in c]
    assert len(transfer_cmd) == 1
    assert "1001" in transfer_cmd[0]
    assert "db.voip" in transfer_cmd[0]


@pytest.mark.asyncio
async def test_blind_transfer_to_named_destination(mock_tool_context):
    from src.tools.telephony.unified_transfer import UnifiedTransferTool
    tool = UnifiedTransferTool()
    mock_tool_context.domain_name = "db.voip"
    # Simulate config with named destinations
    mock_tool_context.config = {
        "tools": {
            "transfer": {
                "destinations": {
                    "vendite": {"extension": "1001", "type": "extension"},
                    "assistenza": {"extension": "1002", "type": "extension"},
                }
            }
        }
    }
    mock_tool_context.get_config_value = lambda key, default=None: {
        "tools.transfer.destinations": {
            "vendite": {"extension": "1001", "type": "extension"},
            "assistenza": {"extension": "1002", "type": "extension"},
        }
    }.get(key, default)

    result = await tool.execute(
        {"destination": "vendite"},
        mock_tool_context,
    )
    assert result["status"] == "success"
    esl = mock_tool_context.esl_client
    transfer_cmd = [c for c in esl.commands_sent if "uuid_transfer" in c]
    assert len(transfer_cmd) == 1
    assert "1001" in transfer_cmd[0]


@pytest.mark.asyncio
async def test_voicemail_transfer(mock_tool_context):
    from src.tools.telephony.voicemail import VoicemailTool
    tool = VoicemailTool()
    mock_tool_context.domain_name = "db.voip"
    mock_tool_context.called_number = "5900"

    result = await tool.execute(
        {"extension": "5900"},
        mock_tool_context,
    )
    assert result["status"] == "success"
    esl = mock_tool_context.esl_client
    transfer_cmd = [c for c in esl.commands_sent if "uuid_transfer" in c]
    assert len(transfer_cmd) == 1
    assert "*99" in transfer_cmd[0]


@pytest.mark.asyncio
async def test_check_extension_status(mock_tool_context):
    from src.tools.telephony.check_extension_status import CheckExtensionStatusTool
    tool = CheckExtensionStatusTool()
    # Mock ESL to return registration data
    mock_tool_context.esl_client.api = lambda cmd: "+OK\n1001@db.voip\tRegistered\n"

    result = await tool.execute(
        {"extension": "1001"},
        mock_tool_context,
    )
    assert "status" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_telephony_tools.py -v
```

Expected: FAIL — imports fail because tools still reference `ari_client`

- [ ] **Step 3: Update ToolExecutionContext**

In `src/tools/context.py`, replace `ari_client` field:

```python
# Replace:
#     ari_client: Any             # ARIClient
# With:
    esl_client: Any              # ESLInboundClient
    outbound_call: Any = None    # OutboundCall (if call came via outbound ESL)
    domain_name: str = "default"
```

- [ ] **Step 4: Port hangup tool**

In `src/tools/telephony/hangup.py`, the tool already works via session flag (`cleanup_after_tts=True`). Just ensure it doesn't reference `ari_client`. If it does, replace with `esl_client`. The actual hangup is done by the engine after TTS completes.

- [ ] **Step 5: Port unified_transfer tool**

In `src/tools/telephony/unified_transfer.py`, replace the transfer logic:

```python
# Replace ARI continue_in_dialplan calls:
#   await context.ari_client.continue_in_dialplan(
#       channel_id, context="from-internal", extension=target)
# With ESL uuid_transfer:

async def _do_transfer(self, context, destination: str, dest_type: str):
    """Execute the transfer via ESL."""
    domain = context.domain_name or "default"
    uuid = context.caller_channel_id

    if dest_type == "extension":
        # Transfer to extension within the FusionPBX domain
        await context.esl_client.transfer(uuid, destination, context=domain)
    elif dest_type == "queue":
        # FusionPBX call center queues
        await context.esl_client.transfer(
            uuid, f"fifo_orbit_{destination}", context=domain
        )
    elif dest_type == "ringgroup":
        # FusionPBX ring groups
        await context.esl_client.transfer(uuid, destination, context=domain)
    else:
        # Direct number/SIP URI
        await context.esl_client.transfer(uuid, destination, context=domain)
```

- [ ] **Step 6: Port voicemail tool**

In `src/tools/telephony/voicemail.py`, replace:

```python
# Replace ARI redirect to voicemail context:
#   await context.ari_client.continue_in_dialplan(channel_id, context="ext-local", extension=vm_ext)
# With FusionPBX voicemail:

uuid = context.caller_channel_id
domain = context.domain_name or "default"
extension = parameters.get("extension", context.called_number)
# FusionPBX voicemail: *99{ext}
vm_dest = f"*99{extension}"
await context.esl_client.transfer(uuid, vm_dest, context=domain)
```

- [ ] **Step 7: Port check_extension_status tool**

In `src/tools/telephony/check_extension_status.py`, replace:

```python
# Replace ARI device state check with ESL registration check:
extension = parameters.get("extension")
domain = context.domain_name or "default"
result = await context.esl_client.api(
    f"sofia status profile internal reg {extension}@{domain}"
)
registered = "+OK" in result and extension in result
return {"status": "registered" if registered else "unregistered", "extension": extension}
```

- [ ] **Step 8: Port live_agent_transfer tool**

In `src/tools/telephony/live_agent_transfer.py`, replace `ari_client` references with `esl_client`. The tool resolves a live agent from config and delegates to `UnifiedTransferTool._do_transfer()`. Ensure config access uses tenant transfer_extensions.

- [ ] **Step 9: Run tests to verify they pass**

```bash
python -m pytest tests/test_telephony_tools.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/tools/ tests/test_telephony_tools.py
git commit -m "feat: port telephony tools from ARI to ESL

Replace ari_client calls with esl_client uuid_* commands.
Transfer uses FusionPBX domain context. Voicemail via *99{ext}."
```

---

### Task 6: Engine Adaptation — Event Handlers

**Files:**
- Modify: `src/engine.py`

This is the largest task. The engine needs targeted changes to replace ARI patterns with ESL.

- [ ] **Step 1: Replace ARI client import and initialization**

In `src/engine.py`, find the ARI client import and init:

```python
# Remove:
from src.ari_client import ARIClient

# Add:
from src.esl_client import ESLInboundClient
from src.esl_outbound_server import OutboundESLServer, OutboundCall
from src.config_freeswitch import FreeSWITCHConfig, TenantResolver
```

In `__init__`, replace:

```python
# Remove:
self.ari_client = ARIClient(
    username=config.asterisk.username,
    password=config.asterisk.password,
    base_url=f"{config.asterisk.scheme}://{config.asterisk.host}:{config.asterisk.port}",
    app_name=config.asterisk.app_name,
    ssl_verify=config.asterisk.ssl_verify,
)

# Add:
fs_config = FreeSWITCHConfig.from_env()
self.esl_client = ESLInboundClient(
    host=fs_config.esl_host,
    port=fs_config.esl_port,
    password=fs_config.esl_password,
)
self.outbound_server = OutboundESLServer(
    host=fs_config.outbound_host,
    port=fs_config.outbound_port,
    on_call=self._handle_inbound_call,
)
self.tenant_resolver = TenantResolver("config/tenants")
```

- [ ] **Step 2: Replace event registration**

Replace the ARI event registrations:

```python
# Remove:
self.ari_client.on_event("StasisStart", self._handle_stasis_start)
self.ari_client.on_event("StasisEnd", self._handle_stasis_end)
self.ari_client.on_event("ChannelDestroyed", self._handle_channel_destroyed)
self.ari_client.on_event("ChannelDtmfReceived", self._handle_dtmf_received)
self.ari_client.on_event("ChannelVarset", self._handle_channel_varset)
self.ari_client.on_event("ChannelTalkingStarted", self._handle_channel_talking_started)
self.ari_client.on_event("ChannelTalkingFinished", self._handle_channel_talking_finished)

# Add:
self.esl_client.on_event("CHANNEL_HANGUP_COMPLETE", self._handle_channel_hangup)
self.esl_client.on_event("CHANNEL_DESTROY", self._handle_channel_destroyed)
self.esl_client.on_event("DTMF", self._handle_dtmf_received)
```

- [ ] **Step 3: Create `_handle_inbound_call` — replaces StasisStart**

This is the main entry point for inbound calls via outbound ESL:

```python
async def _handle_inbound_call(self, call: OutboundCall):
    """Handle an inbound call from FreeSWITCH outbound ESL.

    Replaces _handle_stasis_start + _handle_caller_stasis_start_hybrid.
    """
    try:
        # Step 1: Answer the call
        await call.answer()

        # Step 2: Resolve tenant config
        tenant = self.tenant_resolver.resolve(
            call.domain_name or "default",
            overrides={
                "provider": call.ai_provider,
            }
        )

        # Step 3: Create call session
        session = self._create_call_session(
            channel_id=call.uuid,
            caller_number=call.caller_number,
            caller_name=call.caller_name,
            called_number=call.called_number,
            domain_name=call.domain_name,
            context_name=call.ai_context or "default",
            provider_name=tenant.provider,
        )

        # Step 4: Start AudioSocket for bidirectional audio
        audiosocket_host = self.config.audiosocket.advertise_host or self.config.audiosocket.host
        audiosocket_port = self.config.audiosocket.port
        await call.start_audiosocket(audiosocket_host, audiosocket_port)

        # Step 5: Resolve provider/pipeline and start session
        # (reuse existing provider resolution logic from engine)
        await self._start_provider_session(session, tenant)

    except Exception as e:
        logger.error("Error handling inbound call %s: %s", call.uuid, e, exc_info=True)
        try:
            await call.hangup()
        except Exception:
            pass
```

- [ ] **Step 4: Adapt `_handle_channel_hangup`**

```python
async def _handle_channel_hangup(self, event: dict):
    """Handle CHANNEL_HANGUP_COMPLETE — replaces _handle_stasis_end."""
    uuid = event.get("Unique-ID")
    if not uuid:
        return

    session = self.session_store.get_by_channel(uuid)
    if session:
        await self._cleanup_call(session)
```

- [ ] **Step 5: Adapt `_handle_dtmf_received`**

```python
async def _handle_dtmf_received(self, event: dict):
    """Handle DTMF event — replaces ChannelDtmfReceived handler."""
    uuid = event.get("Unique-ID")
    digit = event.get("DTMF-Digit")
    if not uuid or not digit:
        return

    session = self.session_store.get_by_channel(uuid)
    if session:
        # Delegate to existing DTMF handling logic
        await self._process_dtmf(session, digit)
```

- [ ] **Step 6: Replace bridge logic with direct AudioSocket**

In the original AVA, a mixing bridge is created and both caller + AudioSocket channels are added. With FreeSWITCH outbound ESL, the call is already connected — we just run the `audiosocket` app on the existing channel. No bridge needed.

Remove or skip all `create_bridge`, `add_channel_to_bridge`, `remove_channel_from_bridge` calls. The AudioSocket app handles bidirectional audio directly on the channel.

For transfers where we need to connect caller to a human agent: use `uuid_transfer` which exits the AudioSocket app and routes to the agent's extension.

- [ ] **Step 7: Update startup sequence**

In the `start()` or `run()` method:

```python
# Remove:
await self.ari_client.connect()
await self.ari_client.start_listening()

# Add:
# Start outbound ESL server (for inbound calls)
await self.outbound_server.start()
# Start inbound ESL connection (for outbound calls + global commands)
asyncio.create_task(self.esl_client.start_listening())
# Start AudioSocket server (unchanged)
await self.audiosocket_server.start()
```

- [ ] **Step 8: Update cleanup/shutdown**

```python
# Remove:
await self.ari_client.disconnect()

# Add:
await self.outbound_server.stop()
await self.esl_client.disconnect()
```

- [ ] **Step 9: Replace all remaining `self.ari_client` references**

Search for `self.ari_client` and replace with `self.esl_client`:
- `self.ari_client.hangup_channel(id)` → `self.esl_client.kill(uuid)`
- `self.ari_client.play_media(id, uri)` → `self.esl_client.broadcast(uuid, path)`
- `self.ari_client.set_channel_var(id, k, v)` → `self.esl_client.set_channel_var(uuid, k, v)`
- `self.ari_client.originate_channel(...)` → `self.esl_client.originate(...)`
- Remove all `self.ari_client.create_bridge()` and `add_channel_to_bridge()` calls

- [ ] **Step 10: Update ToolExecutionContext creation**

Everywhere a `ToolExecutionContext` is created, replace `ari_client=self.ari_client` with `esl_client=self.esl_client` and add `domain_name=session.domain_name`.

- [ ] **Step 11: Commit**

```bash
git add src/engine.py
git commit -m "feat: adapt engine from ARI to ESL

Replace ARI event handlers with ESL equivalents.
Inbound calls handled via outbound ESL server.
Remove bridge logic — AudioSocket runs directly on channel.
Tenant resolution from domain_name channel variable."
```

---

### Task 7: Config and Docker Updates

**Files:**
- Modify: `config/ai-agent.yaml`
- Modify: `docker-compose.yml`
- Modify: `Dockerfile`
- Create: `freeswitch/dialplan/ai-agent.xml`

- [ ] **Step 1: Update `config/ai-agent.yaml`**

Replace the `asterisk:` section:

```yaml
# Remove:
# asterisk:
#   app_name: asterisk-ai-voice-agent

# Add:
freeswitch:
  esl_host: ${ESL_HOST:-127.0.0.1}
  esl_port: ${ESL_PORT:-8021}
  esl_password: ${ESL_PASSWORD:-ClueCon}
  outbound_host: ${OUTBOUND_ESL_HOST:-0.0.0.0}
  outbound_port: ${OUTBOUND_ESL_PORT:-8085}

tenants:
  config_dir: config/tenants
```

Update `audiosocket:` section for remote deployment:

```yaml
audiosocket:
  host: 0.0.0.0
  advertise_host: 93.189.136.90  # IP that FreeSWITCH connects to
  port: 8090
  format: slin
```

- [ ] **Step 2: Update `docker-compose.yml`**

```yaml
services:
  ai_engine:
    build:
      context: .
      dockerfile: Dockerfile
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./src:/app/src
      - ./config:/app/config
      - ./models:/app/models
      - ./data:/app/data
    environment:
      - ESL_HOST=${ESL_HOST}
      - ESL_PORT=${ESL_PORT:-8021}
      - ESL_PASSWORD=${ESL_PASSWORD}
      - OUTBOUND_ESL_HOST=${OUTBOUND_ESL_HOST:-0.0.0.0}
      - OUTBOUND_ESL_PORT=${OUTBOUND_ESL_PORT:-8085}
      - AUDIOSOCKET_HOST=${AUDIOSOCKET_HOST:-0.0.0.0}
      - AUDIOSOCKET_PORT=${AUDIOSOCKET_PORT:-8090}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY:-}
      - ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY:-}
      - GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
      - LITELLM_BASE_URL=${LITELLM_BASE_URL:-}
      - LITELLM_API_KEY=${LITELLM_API_KEY:-}
    healthcheck:
      test: ["CMD", "python", "-c", "import socket; s=socket.socket(); s.connect(('127.0.0.1', 8085)); s.close()"]
      interval: 30s
      timeout: 10s
      retries: 3

  local_ai_server:
    build:
      context: ./local_ai_server
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./models:/app/models
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  admin_ui:
    build:
      context: ./admin_ui
    ports:
      - "3003:3003"
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./config:/app/config:ro
      - ./data:/app/data:ro
```

- [ ] **Step 3: Update Dockerfile**

In `Dockerfile`, replace the asterisk group references:

```dockerfile
# Remove:
# ARG ASTERISK_GID=1000
# RUN groupadd -g ${ASTERISK_GID} asterisk && \
#     useradd -m -g asterisk appuser

# Replace with:
RUN useradd -m appuser
USER appuser
```

Ensure `greenswitch` is in requirements.txt (done in Task 1).

- [ ] **Step 4: Create FreeSWITCH dialplan snippet**

Write `freeswitch/dialplan/ai-agent.xml`:

```xml
<!--
  AVA AI Voice Agent — FreeSWITCH/FusionPBX Dialplan Integration

  Add this as a dialplan extension in FusionPBX:
    Menu: Dialplan > Dialplan Manager > Add

  Or place in /etc/freeswitch/dialplan/default/ on voip6.

  Adjust:
    - destination_number: the extension users dial to reach the AI agent
    - socket data: IP:port of the ai_engine outbound ESL server
    - ai_provider / ai_context: per-call overrides (optional)
-->
<include>
  <extension name="ai-agent-inbound">
    <condition field="destination_number" expression="^(5900)$">
      <!-- Optional: set AI provider and context per extension -->
      <action application="set" data="ai_provider=litellm_hybrid"/>
      <action application="set" data="ai_context=receptionist"/>
      <!-- Connect to AVA ai_engine outbound ESL server -->
      <action application="socket" data="93.189.136.90:8085 async full"/>
    </condition>
  </extension>

  <!-- Example: different context for support line -->
  <extension name="ai-agent-support">
    <condition field="destination_number" expression="^(5901)$">
      <action application="set" data="ai_provider=litellm_hybrid"/>
      <action application="set" data="ai_context=support"/>
      <action application="socket" data="93.189.136.90:8085 async full"/>
    </condition>
  </extension>
</include>
```

- [ ] **Step 5: Commit**

```bash
git add config/ai-agent.yaml docker-compose.yml Dockerfile freeswitch/ .env.example
git commit -m "feat: update config, Docker, and dialplan for FusionPBX

Replace Asterisk config with FreeSWITCH ESL settings.
Add tenant config directory and dialplan XML snippets.
Update Docker compose for ESL-based deployment on aicore."
```

---

### Task 8: Integration Test — End-to-End Call Flow

**Files:**
- Create: `tests/test_integration_call_flow.py`

- [ ] **Step 1: Write integration test**

Write `tests/test_integration_call_flow.py`:

```python
"""Integration test: simulates a full inbound call flow without real FreeSWITCH."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from tests.conftest import MockESLClient, MockSession


@pytest.mark.asyncio
async def test_inbound_call_flow():
    """Simulate: FS connects via outbound ESL → answer → AudioSocket → AI → hangup."""
    from src.esl_outbound_server import OutboundCall

    # Mock the outbound ESL connection
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock(body="+OK"))

    variables = {
        "Unique-ID": "call-uuid-001",
        "variable_domain_name": "db.voip",
        "variable_ai_provider": "litellm_hybrid",
        "variable_ai_context": "receptionist",
        "Caller-Caller-ID-Number": "+391234567890",
        "Caller-Caller-ID-Name": "Mario Rossi",
        "Caller-Destination-Number": "5900",
    }

    async def get_var(name):
        return variables.get(name)

    call = OutboundCall(conn=mock_conn, get_variable=get_var)
    await call.load_variables()

    # Verify variables loaded
    assert call.uuid == "call-uuid-001"
    assert call.domain_name == "db.voip"
    assert call.caller_number == "+391234567890"

    # Answer
    await call.answer()
    mock_conn.execute.assert_any_call("answer")

    # Start AudioSocket
    await call.start_audiosocket("93.189.136.90", 8090)
    # Verify audiosocket command was sent
    calls = mock_conn.execute.call_args_list
    audiosocket_call = [c for c in calls if "audiosocket" in str(c).lower()]
    assert len(audiosocket_call) >= 1

    # Hangup
    await call.hangup()
    mock_conn.execute.assert_any_call("hangup", "NORMAL_CLEARING")


@pytest.mark.asyncio
async def test_tenant_resolution_in_call():
    """Verify tenant config is correctly resolved from call domain."""
    import tempfile, os
    from src.config_freeswitch import TenantResolver

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write tenant configs
        with open(os.path.join(tmpdir, "db.voip.yaml"), "w") as f:
            f.write(
                "domain: db.voip\n"
                "provider: litellm_hybrid\n"
                "language: it\n"
                "tts_voice: it_male\n"
                "context_prompt: Assistente Tecnoadsl\n"
                "greeting: Buongiorno!\n"
                "transfer_extensions:\n"
                "  vendite: '1001'\n"
                "  assistenza: '1002'\n"
            )
        with open(os.path.join(tmpdir, "default.yaml"), "w") as f:
            f.write(
                "domain: default\n"
                "provider: local\n"
                "language: en\n"
                "tts_voice: en_male\n"
                "context_prompt: Default\n"
            )

        resolver = TenantResolver(tmpdir)

        # Known domain
        tenant = resolver.resolve("db.voip")
        assert tenant.provider == "litellm_hybrid"
        assert tenant.language == "it"
        assert tenant.transfer_extensions["vendite"] == "1001"

        # Unknown domain falls back
        tenant = resolver.resolve("unknown.domain")
        assert tenant.provider == "local"
        assert tenant.language == "en"

        # Override from channel variable
        tenant = resolver.resolve("db.voip", overrides={"provider": "openai_realtime"})
        assert tenant.provider == "openai_realtime"
        assert tenant.language == "it"  # non-overridden fields preserved


@pytest.mark.asyncio
async def test_esl_inbound_event_dispatch():
    """Verify ESL client dispatches events to registered handlers."""
    from src.esl_client import ESLInboundClient

    client = ESLInboundClient(host="127.0.0.1", port=8021, password="ClueCon")
    hangup_handler = AsyncMock()
    dtmf_handler = AsyncMock()

    client.on_event("CHANNEL_HANGUP_COMPLETE", hangup_handler)
    client.on_event("DTMF", dtmf_handler)

    # Dispatch hangup
    await client._dispatch_event({
        "Event-Name": "CHANNEL_HANGUP_COMPLETE",
        "Unique-ID": "uuid-001",
        "Hangup-Cause": "NORMAL_CLEARING",
    })
    hangup_handler.assert_called_once()
    dtmf_handler.assert_not_called()

    # Dispatch DTMF
    await client._dispatch_event({
        "Event-Name": "DTMF",
        "Unique-ID": "uuid-001",
        "DTMF-Digit": "5",
    })
    dtmf_handler.assert_called_once()


@pytest.mark.asyncio
async def test_transfer_tool_uses_domain_context():
    """Verify transfer sends uuid_transfer with correct FusionPBX domain."""
    from tests.conftest import MockESLClient, MockToolContext, MockSession

    esl = MockESLClient()
    session = MockSession(domain_name="norcia.voip6")
    ctx = MockToolContext()
    ctx.esl_client = esl
    ctx._session = session
    ctx.domain_name = "norcia.voip6"

    from src.tools.telephony.unified_transfer import UnifiedTransferTool
    tool = UnifiedTransferTool()
    result = await tool.execute(
        {"destination": "2001", "type": "extension"},
        ctx,
    )

    transfer_cmd = [c for c in esl.commands_sent if "uuid_transfer" in c]
    assert len(transfer_cmd) == 1
    assert "norcia.voip6" in transfer_cmd[0]
    assert "2001" in transfer_cmd[0]
```

- [ ] **Step 2: Run integration tests**

```bash
python -m pytest tests/test_integration_call_flow.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS (test_tenant_config, test_esl_client, test_esl_outbound_server, test_telephony_tools, test_integration_call_flow)

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration_call_flow.py
git commit -m "test: add integration tests for end-to-end call flow

Tests cover: outbound ESL call handling, tenant resolution,
ESL event dispatch, transfer with FusionPBX domain context."
```

---

### Task 9: Cleanup and Documentation

**Files:**
- Modify: `README.md`
- Remove: any remaining Asterisk-only references

- [ ] **Step 1: Search for remaining Asterisk references**

```bash
grep -rn "asterisk\|Asterisk\|ARI\|ari_client\|Stasis\|stasis" src/ --include="*.py" | grep -v "__pycache__"
```

Fix any remaining references: replace `ari_client` with `esl_client`, `Asterisk` with `FreeSWITCH`, `ARI` with `ESL`, remove dead imports.

- [ ] **Step 2: Update README.md**

Replace Asterisk setup instructions with FreeSWITCH/FusionPBX:
- Title: "AVA AI Voice Agent for FusionPBX"
- Prerequisites: FusionPBX with mod_event_socket + mod_audiosocket
- Config: ESL credentials in `.env`, tenant YAMLs in `config/tenants/`
- Dialplan: reference `freeswitch/dialplan/ai-agent.xml`
- Quick start: `cp .env.example .env && docker compose up -d`

- [ ] **Step 3: Verify Docker build**

```bash
cd /Users/daniele/Desktop/AVA-FusionPBX
docker compose build ai_engine
```

Expected: builds successfully

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: cleanup Asterisk references, update README for FusionPBX

Remove all remaining ARI/Asterisk references from source.
Update documentation for FreeSWITCH/FusionPBX deployment."
```

---

## Summary

| Task | Description | Key Files |
|------|-------------|-----------|
| 1 | Project setup, clone AVA, remove ARI files | requirements.txt, conftest.py |
| 2 | FreeSWITCH config + multi-tenant resolver | config_freeswitch.py, tenants/*.yaml |
| 3 | ESL inbound client | esl_client.py |
| 4 | Outbound ESL server | esl_outbound_server.py |
| 5 | Port telephony tools to ESL | tools/telephony/*.py |
| 6 | Adapt engine event handlers | engine.py |
| 7 | Config, Docker, dialplan | ai-agent.yaml, docker-compose.yml, ai-agent.xml |
| 8 | Integration tests | test_integration_call_flow.py |
| 9 | Cleanup and docs | README.md, grep cleanup |
