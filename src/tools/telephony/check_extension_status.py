"""
Check Extension Status Tool - Query FreeSWITCH registration state for an extension.

Purpose:
- Allow the AI agent to check whether an internal extension is registered/available
  before attempting a transfer.

Uses ESL `sofia status profile internal reg {extension}@{domain}` to check
registration state on FusionPBX/FreeSWITCH.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import structlog

from src.tools.base import Tool, ToolDefinition, ToolParameter, ToolCategory, ToolPhase
from src.tools.context import ToolExecutionContext

logger = structlog.get_logger(__name__)


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, tuple):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _looks_like_extension_number(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and value.isdigit()


def _resolve_extension_entry(
    *,
    target: str,
    extensions_config: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve a user-supplied target to a configured extension entry."""
    target = (target or "").strip()
    if not target or not isinstance(extensions_config, dict):
        return "", {}, ""

    if target in extensions_config and isinstance(extensions_config.get(target), dict):
        return target, dict(extensions_config[target]), "config.key"

    target_lower = target.lower()
    for ext_num, ext_cfg in extensions_config.items():
        if not isinstance(ext_num, str):
            ext_num = str(ext_num)
        if not isinstance(ext_cfg, dict):
            continue
        name = str(ext_cfg.get("name", "") or "").strip().lower()
        if name and name == target_lower:
            return ext_num, dict(ext_cfg), "config.name"

        aliases = [a.strip().lower() for a in _as_str_list(ext_cfg.get("aliases")) if a.strip()]
        if target_lower in aliases:
            return ext_num, dict(ext_cfg), "config.alias"

    return "", {}, ""


def _resolve_transfer_destination_extension(
    *,
    target: str,
    destinations: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve a transfer destination key to an extension number."""
    target = (target or "").strip()
    if not target or not isinstance(destinations, dict):
        return "", {}, ""

    dest = destinations.get(target)
    if not isinstance(dest, dict):
        return "", {}, ""

    if str(dest.get("type", "") or "").strip().lower() != "extension":
        return "", {}, ""

    ext = str(dest.get("target", "") or "").strip()
    if not _looks_like_extension_number(ext):
        return "", {}, ""

    return ext, dict(dest), "config.transfer.destinations"


def _allowed_configured_extensions(
    *,
    extensions_config: Dict[str, Any],
    destinations: Dict[str, Any],
) -> List[str]:
    allowed: set[str] = set()

    if isinstance(extensions_config, dict):
        for key, cfg in extensions_config.items():
            extension = str(key or "").strip()
            if not _looks_like_extension_number(extension):
                continue
            if isinstance(cfg, dict) and cfg.get("transfer") is False:
                continue
            allowed.add(extension)

    if isinstance(destinations, dict):
        for cfg in destinations.values():
            if not isinstance(cfg, dict):
                continue
            if str(cfg.get("type", "") or "").strip().lower() != "extension":
                continue
            extension = str(cfg.get("target", "") or "").strip()
            if _looks_like_extension_number(extension):
                allowed.add(extension)

    return sorted(allowed)


def _parse_sofia_registration(output: str, extension: str, domain: str) -> Dict[str, Any]:
    """Parse `sofia status profile ... reg` output to determine registration state."""
    if not output:
        return {"registered": False, "raw": ""}

    search_key = f"{extension}@{domain}"
    lines = output.strip().splitlines()

    for line in lines:
        if search_key in line:
            return {"registered": True, "raw": line.strip()}

    # If the output contains "Total items returned: 0" or no match
    return {"registered": False, "raw": output.strip()[:200]}


class CheckExtensionStatusTool(Tool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="check_extension_status",
            description=(
                "Check if an internal extension is registered (available) by querying "
                "FreeSWITCH SIP registration state via ESL. "
                "Use this before attempting a transfer to a live agent. "
                "Only check extension numbers configured under Tools unless the guardrail is explicitly disabled."
            ),
            category=ToolCategory.TELEPHONY,
            phase=ToolPhase.IN_CALL,
            is_global=False,
            requires_channel=False,
            max_execution_time=10,
            parameters=[
                ToolParameter(
                    name="extension",
                    type="string",
                    description="Extension number to check (e.g., '1001').",
                    required=True,
                ),
            ],
        )

    async def execute(self, parameters: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        await self.validate_parameters(parameters)

        if not context.esl_client:
            return {"status": "error", "message": "ESL client not available in tool context"}

        target = str(parameters.get("extension", "") or "").strip()
        domain = context.domain_name or "default"

        tool_cfg = context.get_config_value("tools.check_extension_status", {}) or {}
        extensions_cfg = context.get_config_value("tools.extensions.internal", {}) or {}
        transfer_destinations = context.get_config_value("tools.transfer.destinations", {}) or {}
        restrict_to_configured = bool(tool_cfg.get("restrict_to_configured_extensions", True))
        allowed_extensions = _allowed_configured_extensions(
            extensions_config=extensions_cfg,
            destinations=transfer_destinations,
        )

        # Resolve target to extension number
        resolved_ext = ""
        ext_source = ""

        if _looks_like_extension_number(target):
            resolved_ext = target
            ext_source = "parameter.extension"
        else:
            resolved_ext, _, ext_source = _resolve_extension_entry(
                target=target, extensions_config=extensions_cfg
            )
            if not resolved_ext:
                resolved_ext, _, ext_source = _resolve_transfer_destination_extension(
                    target=target, destinations=transfer_destinations
                )

        extension = resolved_ext or target

        # Guardrail: restrict to configured extensions
        if restrict_to_configured:
            if not allowed_extensions:
                return {
                    "status": "failed",
                    "message": "No configured extensions are available for status checks.",
                    "extension": extension,
                    "available": False,
                    "guardrail_blocked": True,
                    "allowed_extensions": allowed_extensions,
                }

            if _looks_like_extension_number(extension) and extension not in allowed_extensions:
                return {
                    "status": "failed",
                    "message": (
                        f"Extension {extension} is not configured. "
                        f"Allowed extensions: {', '.join(allowed_extensions)}."
                    ),
                    "extension": extension,
                    "available": False,
                    "guardrail_blocked": True,
                    "allowed_extensions": allowed_extensions,
                }

        # Query FreeSWITCH registration via ESL
        try:
            result = await context.esl_client.api(
                f"sofia status profile internal reg {extension}@{domain}"
            )
        except Exception as exc:
            logger.warning(
                "ESL registration query failed",
                call_id=context.call_id,
                extension=extension,
                domain=domain,
                error=str(exc),
            )
            return {
                "status": "error",
                "message": f"Failed to query registration state: {exc}",
                "extension": extension,
            }

        parsed = _parse_sofia_registration(result or "", extension, domain)
        available = parsed["registered"]

        logger.info(
            "Extension registration status",
            call_id=context.call_id,
            target=target,
            extension=extension,
            domain=domain,
            registered=available,
            source=ext_source,
        )

        return {
            "status": "success",
            "target": target,
            "extension": extension,
            "domain": domain,
            "resolution_source": ext_source,
            "available": available,
            "registered": available,
        }
