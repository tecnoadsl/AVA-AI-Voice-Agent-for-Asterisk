"""FreeSWITCH and multi-tenant configuration models."""
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, Optional

import yaml


@dataclass
class FreeSWITCHConfig:
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
