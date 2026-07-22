from __future__ import annotations

import pytest

from oracle_bi.settings import config_from_secret_mapping


BI_VALUES = {
    "auth_server": "https://auth.example",
    "application_server": "https://app.example",
    "org_identifier": "BYC",
    "client_id": "client",
    "username": "bi-user",
    "password": "secret",
}


def test_oracle_bi_section_is_preferred_over_legacy_oracle() -> None:
    config = config_from_secret_mapping(
        {
            "oracle_bi": BI_VALUES,
            "oracle": {**BI_VALUES, "username": "legacy-user"},
            "oracle_labor": {"api_token": "labor-token", "password": "labor"},
        }
    )
    assert config.username == "bi-user"


def test_legacy_oracle_section_remains_supported() -> None:
    config = config_from_secret_mapping({"oracle": BI_VALUES})
    assert config.org_identifier == "BYC"


def test_labor_section_is_never_used_as_bi_configuration() -> None:
    with pytest.raises(ValueError, match="oracle_bi"):
        config_from_secret_mapping(
            {"oracle_labor": {"api_token": "token", "password": "password"}}
        )


def test_standalone_bi_toml_file_is_supported(tmp_path) -> None:
    from oracle_bi.settings import config_from_toml_file

    path = tmp_path / "bi_secrets.toml"
    path.write_text(
        """
[oracle_bi]
auth_server = "https://auth.example"
application_server = "https://app.example"
org_identifier = "BYC"
client_id = "client"
username = "bi-user"
password = "secret"
""".strip(),
        encoding="utf-8",
    )

    config = config_from_toml_file(path)
    assert config.username == "bi-user"
    assert config.application_server == "https://app.example"
