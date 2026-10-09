from src.integrations import INTEGRATION_PRESETS


def test_github_preset_is_bearer_with_api_base():
    p = INTEGRATION_PRESETS["github"]
    assert p["auth_type"] == "bearer"
    assert p["base_url"] == "https://api.github.com"
    assert "/repos/{owner}/{repo}/commits" in p["description"]


def test_trello_preset_uses_query_token_and_default_key_param():
    p = INTEGRATION_PRESETS["trello"]
    assert p["auth_type"] == "query"
    assert p["auth_param"] == "token"
    assert p["base_url"] == "https://api.trello.com"
    assert "default_params" in p["description"] and "key" in p["description"]
