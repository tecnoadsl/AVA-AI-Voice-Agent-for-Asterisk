"""
Tests for telephony tools ported to ESL.

Verifies that hangup, blind_transfer, voicemail, and check_extension_status
use ESL commands instead of ARI.
"""
import pytest
from tests.conftest import MockESLClient, MockToolContext, MockSession


# ---------------------------------------------------------------------------
# Hangup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hangup_sets_cleanup_after_tts():
    from src.tools.telephony.hangup import HangupCallTool

    esl = MockESLClient()
    session = MockSession()
    ctx = MockToolContext(esl_client=esl, _session=session)

    tool = HangupCallTool()
    result = await tool.execute({"farewell_message": "Bye!"}, ctx)

    assert result["status"] == "success"
    assert result["will_hangup"] is True
    assert session.cleanup_after_tts is True


# ---------------------------------------------------------------------------
# Blind Transfer (extension)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blind_transfer_extension_sends_uuid_transfer():
    from src.tools.telephony.unified_transfer import UnifiedTransferTool

    esl = MockESLClient()
    session = MockSession()
    ctx = MockToolContext(
        esl_client=esl,
        _session=session,
        domain_name="example.voip6.tecnoadsl.net",
        config={
            "tools.transfer": {
                "enabled": True,
                "destinations": {
                    "reception": {
                        "type": "extension",
                        "target": "1001",
                        "description": "Reception desk",
                    }
                },
            }
        },
    )

    tool = UnifiedTransferTool()
    result = await tool.execute({"destination": "reception"}, ctx)

    assert result["status"] == "success"
    assert result["type"] == "extension"
    # Verify ESL transfer was called with domain context
    assert any(
        "transfer:" in cmd and "1001" in cmd and "example.voip6.tecnoadsl.net" in cmd
        for cmd in esl.commands_sent
    ), f"Expected ESL transfer command, got: {esl.commands_sent}"


@pytest.mark.asyncio
async def test_blind_transfer_queue_sends_fifo_orbit():
    from src.tools.telephony.unified_transfer import UnifiedTransferTool

    esl = MockESLClient()
    session = MockSession()
    ctx = MockToolContext(
        esl_client=esl,
        _session=session,
        domain_name="example.voip6.tecnoadsl.net",
        config={
            "tools.transfer": {
                "enabled": True,
                "destinations": {
                    "support_queue": {
                        "type": "queue",
                        "target": "5000",
                        "description": "Support queue",
                    }
                },
            }
        },
    )

    tool = UnifiedTransferTool()
    result = await tool.execute({"destination": "support_queue"}, ctx)

    assert result["status"] == "success"
    assert result["type"] == "queue"
    assert any("fifo_orbit_5000" in cmd for cmd in esl.commands_sent)


@pytest.mark.asyncio
async def test_blind_transfer_ringgroup():
    from src.tools.telephony.unified_transfer import UnifiedTransferTool

    esl = MockESLClient()
    session = MockSession()
    ctx = MockToolContext(
        esl_client=esl,
        _session=session,
        domain_name="example.voip6.tecnoadsl.net",
        config={
            "tools.transfer": {
                "enabled": True,
                "destinations": {
                    "sales": {
                        "type": "ringgroup",
                        "target": "600",
                        "description": "Sales ring group",
                    }
                },
            }
        },
    )

    tool = UnifiedTransferTool()
    result = await tool.execute({"destination": "sales"}, ctx)

    assert result["status"] == "success"
    assert result["type"] == "ringgroup"
    assert any("transfer:" in cmd and "600" in cmd for cmd in esl.commands_sent)


# ---------------------------------------------------------------------------
# Voicemail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voicemail_sends_star99_transfer():
    from src.tools.telephony.voicemail import VoicemailTool

    esl = MockESLClient()
    session = MockSession()
    ctx = MockToolContext(
        esl_client=esl,
        _session=session,
        domain_name="example.voip6.tecnoadsl.net",
        config={
            "tools.leave_voicemail": {
                "extension": "1001",
            }
        },
    )

    tool = VoicemailTool()
    result = await tool.execute({}, ctx)

    assert result["status"] == "success"
    assert any(
        "*991001" in cmd and "example.voip6.tecnoadsl.net" in cmd
        for cmd in esl.commands_sent
    ), f"Expected *991001 transfer, got: {esl.commands_sent}"


@pytest.mark.asyncio
async def test_voicemail_not_configured():
    from src.tools.telephony.voicemail import VoicemailTool

    esl = MockESLClient()
    ctx = MockToolContext(esl_client=esl, config={})

    tool = VoicemailTool()
    result = await tool.execute({}, ctx)

    assert result["status"] == "failed"
    assert len(esl.commands_sent) == 0


# ---------------------------------------------------------------------------
# Check Extension Status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_extension_status_calls_sofia():
    from src.tools.telephony.check_extension_status import CheckExtensionStatusTool

    esl = MockESLClient()
    session = MockSession()
    ctx = MockToolContext(
        esl_client=esl,
        _session=session,
        domain_name="example.voip6.tecnoadsl.net",
        config={
            "tools.check_extension_status": {"restrict_to_configured_extensions": False},
        },
    )

    tool = CheckExtensionStatusTool()
    result = await tool.execute({"extension": "1001"}, ctx)

    assert result["status"] == "success"
    assert any(
        "sofia status profile internal reg 1001@example.voip6.tecnoadsl.net" in cmd
        for cmd in esl.commands_sent
    ), f"Expected sofia reg query, got: {esl.commands_sent}"


@pytest.mark.asyncio
async def test_check_extension_status_guardrail_blocks_unconfigured():
    from src.tools.telephony.check_extension_status import CheckExtensionStatusTool

    esl = MockESLClient()
    ctx = MockToolContext(
        esl_client=esl,
        domain_name="example.voip6.tecnoadsl.net",
        config={
            "tools.extensions.internal": {
                "1001": {"name": "Reception"},
            },
        },
    )

    tool = CheckExtensionStatusTool()
    result = await tool.execute({"extension": "9999"}, ctx)

    assert result["status"] == "failed"
    assert result.get("guardrail_blocked") is True
    # No ESL command should have been sent
    assert len(esl.commands_sent) == 0


@pytest.mark.asyncio
async def test_check_extension_no_esl_client():
    from src.tools.telephony.check_extension_status import CheckExtensionStatusTool

    ctx = MockToolContext(
        esl_client=None,
        config={
            "tools.check_extension_status": {"restrict_to_configured_extensions": False},
        },
    )

    tool = CheckExtensionStatusTool()
    result = await tool.execute({"extension": "1001"}, ctx)

    assert result["status"] == "error"
    assert "ESL" in result["message"]
