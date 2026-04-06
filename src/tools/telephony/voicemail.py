"""
Voicemail Tool - Route calls to voicemail.

This tool allows the AI to send callers to voicemail when requested.

FusionPBX voicemail is reached by transferring to *99{extension} within the
tenant domain context via ESL uuid_transfer.
"""

from typing import Dict, Any
import structlog

from ..base import Tool, ToolDefinition, ToolParameter, ToolCategory
from ..context import ToolExecutionContext

logger = structlog.get_logger(__name__)


class VoicemailTool(Tool):
    """
    Tool for sending callers to voicemail.

    Uses ESL uuid_transfer to *99{extension} in the FusionPBX domain context.
    """
    
    @property
    def definition(self) -> ToolDefinition:
        """Return tool definition."""
        return ToolDefinition(
            name="leave_voicemail",
            description="Send the caller to voicemail so they can leave a message",
            category=ToolCategory.TELEPHONY,
            requires_channel=True,
            max_execution_time=15,
            parameters=[]  # No parameters - uses config
        )
    
    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute voicemail transfer.
        
        Args:
            parameters: Empty dict (no parameters)
            context: Tool execution context
        
        Returns:
            Dict with status and message
        """
        # Get voicemail config
        config = context.get_config_value("tools.leave_voicemail")
        if not config:
            logger.warning("Voicemail tool not configured", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Voicemail is not available",
            }

        extension = config.get('extension')
        if not extension:
            logger.error("Voicemail extension not configured", call_id=context.call_id)
            return {
                "status": "failed",
                "message": "Voicemail is not configured properly"
            }

        domain = context.domain_name or "default"

        logger.info(
            "Voicemail transfer requested",
            call_id=context.call_id,
            extension=extension,
            domain=domain,
        )

        # Set transfer_active flag BEFORE the transfer command
        await context.update_session(
            transfer_active=True,
            transfer_target=f"Voicemail {extension}"
        )

        try:
            # FusionPBX voicemail: *99{extension} in the domain context
            vm_destination = f"*99{extension}"

            await context.esl_client.transfer(
                context.caller_channel_id, vm_destination, context=domain
            )

            logger.info(
                "Voicemail transfer executed",
                call_id=context.call_id,
                destination=vm_destination,
                domain=domain,
            )

            return {
                "status": "success",
                "message": "Transferring you to voicemail now. Please leave a message after the tone."
            }

        except Exception as e:
            logger.error(
                "Voicemail transfer failed",
                call_id=context.call_id,
                error=str(e),
                exc_info=True
            )

            # Clear transfer flag on failure
            await context.update_session(
                transfer_active=False,
                transfer_target=None
            )

            return {
                "status": "failed",
                "message": "Unable to transfer to voicemail at this time"
            }
