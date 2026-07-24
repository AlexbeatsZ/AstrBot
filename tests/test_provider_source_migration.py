from astrbot.core.utils.migra_helper import _migra_provider_to_source_structure


class _Config(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_count = 0

    def save_config(self) -> None:
        self.save_count += 1


def test_provider_migration_uses_clean_shared_source_id() -> None:
    """Legacy models from one backend should share a clean source ID."""
    config = _Config(
        provider_sources=[],
        provider=[
            {
                "id": "agy/gemini-3.5-flash",
                "type": "agy_cli_chat_completion",
                "provider_type": "chat_completion",
                "model": "gemini-3.5-flash",
                "proxy": "http://host.docker.internal:7897",
                "enable": True,
            },
            {
                "id": "agy/gemini-3.1-pro",
                "type": "agy_cli_chat_completion",
                "provider_type": "chat_completion",
                "model": "gemini-3.1-pro",
                "proxy": "http://host.docker.internal:7897",
                "enable": True,
            },
        ],
    )

    _migra_provider_to_source_structure(config)

    assert [source["id"] for source in config["provider_sources"]] == ["agy"]
    assert {provider["provider_source_id"] for provider in config["provider"]} == {
        "agy"
    }
    assert config.save_count == 1
