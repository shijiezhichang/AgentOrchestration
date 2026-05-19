import os
import pytest
from src.common.config import Config, ConfigError


class TestConfig:
    def test_load_config(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"app": {"name": "test", "port": 8080}}')
        config = Config(str(config_file))
        assert config.get("app.name") == "test"
        assert config.get("app.port") == 8080

    def test_default_value(self):
        config = Config()
        assert config.get("nonexistent.key", "default") == "default"

    def test_set_value(self):
        config = Config()
        config.set("database.host", "localhost")
        assert config.get("database.host") == "localhost"

    def test_nested_set(self):
        config = Config()
        config.set("a.b.c.d", "value")
        assert config.get("a.b.c.d") == "value"

    def test_to_dict(self):
        config = Config()
        config.set("key1", "value1")
        config.set("key2", "value2")
        data = config.to_dict()
        assert data["key1"] == "value1"
        assert data["key2"] == "value2"

    # ── Regression tests for Issue #12: Config merge safety ──

    def test_env_scalar_replacing_branch_raises_error(self, tmp_path, monkeypatch):
        """Env AO_APP=foo must not silently replace app.name from file."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"app": {"name": "myapp"}}')
        monkeypatch.setenv("AO_APP", "overwrite_attempt")
        with pytest.raises(ConfigError, match="nested branch"):
            Config(str(config_file))

    def test_midwalk_scalar_blocks_deeper_nesting(self):
        """If 'a' is a scalar, setting 'a.b.c' must raise ConfigError."""
        config = Config()
        config.set("a", 1)
        with pytest.raises(ConfigError, match="scalar"):
            config.set("a.b.c", "deep")

    def test_env_deeper_nested_override_works(self, tmp_path, monkeypatch):
        """AO_APP_NAME should override app.name without conflict."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"app": {"name": "default", "port": 8080}}')
        monkeypatch.setenv("AO_APP_NAME", "overridden")
        config = Config(str(config_file))
        assert config.get("app.name") == "overridden"
        assert config.get("app.port") == 8080  # sibling untouched

    def test_env_new_scalar_no_conflict(self, monkeypatch):
        """AO_FOO=bar with no existing branch should work fine."""
        monkeypatch.setenv("AO_FOO", "bar")
        config = Config()
        assert config.get("foo") == "bar"

    def test_env_new_nested_branch_no_conflict(self, monkeypatch):
        """AO_NEW_BRANCH_KEY=hello creates nested branch."""
        monkeypatch.setenv("AO_NEW_BRANCH_KEY", "hello")
        config = Config()
        assert config.get("new.branch.key") == "hello"

    def test_set_scalar_over_branch_raises(self):
        """Config.set('app', 'scalar') over existing dict branch must raise."""
        config = Config()
        config.set("app.name", "myapp")
        with pytest.raises(ConfigError, match="nested branch"):
            config.set("app", "overwrite")

    def test_deep_branch_protection(self, tmp_path, monkeypatch):
        """AO_A_B replaces a.b branch — must raise even for deep structures."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"a": {"b": {"c": 1, "d": 2}}}')
        monkeypatch.setenv("AO_A_B", "scalar")
        with pytest.raises(ConfigError, match="nested branch"):
            Config(str(config_file))
