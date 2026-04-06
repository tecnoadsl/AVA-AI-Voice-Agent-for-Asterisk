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
    assert tenant.language == "en"
