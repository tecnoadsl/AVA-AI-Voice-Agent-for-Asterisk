"""
Shared test fixtures for AVA FusionPBX port.

Provides mock ESL client, session, and tool context objects for unit tests.
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class MockESLClient:
    """Mock FreeSWITCH ESL client that records every command sent."""

    def __init__(self):
        self.commands_sent: List[str] = []
        self.connected: bool = False

    def _record(self, cmd: str) -> None:
        self.commands_sent.append(cmd)

    async def connect(self) -> None:
        self.connected = True
        self._record("connect")

    async def disconnect(self) -> None:
        self.connected = False
        self._record("disconnect")

    async def send_command(self, command: str, *args, **kwargs) -> Optional[str]:
        self._record(f"send_command:{command}")
        return None

    async def api(self, command: str, *args, **kwargs) -> Optional[str]:
        self._record(f"api:{command}")
        return None

    async def bgapi(self, command: str, *args, **kwargs) -> Optional[str]:
        self._record(f"bgapi:{command}")
        return None

    async def answer(self, channel_id: str) -> None:
        self._record(f"answer:{channel_id}")

    async def hangup(self, channel_id: str, cause: str = "NORMAL_CLEARING") -> None:
        self._record(f"hangup:{channel_id}:{cause}")

    async def transfer(self, channel_id: str, destination: str, context: str = "default") -> None:
        self._record(f"transfer:{channel_id}:{destination}:{context}")

    async def bridge(self, channel_id: str, destination: str) -> None:
        self._record(f"bridge:{channel_id}:{destination}")

    async def originate(self, destination: str, **kwargs) -> Optional[str]:
        self._record(f"originate:{destination}")
        return None

    async def playback(self, channel_id: str, file_path: str) -> None:
        self._record(f"playback:{channel_id}:{file_path}")

    async def get_channel_var(self, channel_id: str, var_name: str) -> Optional[str]:
        self._record(f"get_channel_var:{channel_id}:{var_name}")
        return None

    async def set_channel_var(self, channel_id: str, var_name: str, value: str) -> None:
        self._record(f"set_channel_var:{channel_id}:{var_name}:{value}")

    async def broadcast(self, channel_id: str, file_path: str) -> None:
        self._record(f"broadcast:{channel_id}:{file_path}")

    async def kill(self, channel_id: str) -> None:
        self._record(f"kill:{channel_id}")

    def last_command(self) -> Optional[str]:
        """Return the most recently recorded command, or None if empty."""
        return self.commands_sent[-1] if self.commands_sent else None


@dataclass
class MockSession:
    """Lightweight mock of a call session."""

    call_id: str = "test-call-id"
    caller_channel_id: str = "test-caller-channel"
    bridge_id: str = "test-bridge-id"
    caller_number: str = "+39000000000"
    called_number: str = "+39111111111"
    caller_name: str = "Test Caller"
    domain_name: str = "test.voip6.tecnoadsl.net"
    provider_name: str = "test_provider"
    cleanup_after_tts: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MockToolContext:
    """Mock tool context providing ESL client and session access."""

    call_id: str = "test-call-id"
    caller_channel_id: str = "test-caller-channel"
    bridge_id: str = "test-bridge-id"
    caller_number: str = "+39000000000"
    called_number: str = "+39111111111"
    caller_name: str = "Test Caller"
    context_name: str = "default"
    domain_name: str = "test.voip6.tecnoadsl.net"
    esl_client: Any = field(default_factory=MockESLClient)
    config: Dict[str, Any] = field(default_factory=dict)
    provider_name: str = "test_provider"
    provider_session: Any = None
    session_store: Dict[str, Any] = field(default_factory=dict)
    _session: Optional[MockSession] = field(default=None, repr=False)

    async def get_session(self) -> Optional[MockSession]:
        return self._session

    async def update_session(self, **kwargs) -> None:
        if self._session is None:
            self._session = MockSession()
        for k, v in kwargs.items():
            if hasattr(self._session, k):
                setattr(self._session, k, v)
            else:
                self._session.extra[k] = v

    def get_config_value(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_esl() -> MockESLClient:
    """Return a fresh MockESLClient instance."""
    return MockESLClient()


@pytest.fixture
def mock_session() -> MockSession:
    """Return a fresh MockSession instance."""
    return MockSession()


@pytest.fixture
def mock_tool_context(mock_esl: MockESLClient, mock_session: MockSession) -> MockToolContext:
    """Return a MockToolContext wired to mock_esl and mock_session."""
    ctx = MockToolContext(esl_client=mock_esl)
    ctx._session = mock_session
    return ctx
