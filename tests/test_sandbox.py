import pytest
from src.agent.sandbox import ResourceLimits, AgentSandbox


class TestResourceLimits:
    def test_default_values(self):
        limits = ResourceLimits()
        assert limits.cpu_time == 60
        assert limits.memory_mb == 512
        assert limits.disk_mb == 100

    def test_custom_values(self):
        limits = ResourceLimits(cpu_time=30, memory_mb=256, disk_mb=50)
        assert limits.cpu_time == 30
        assert limits.memory_mb == 256
        assert limits.disk_mb == 50

    def test_negative_cpu_time_raises(self):
        with pytest.raises(ValueError, match="cpu_time.*positive"):
            ResourceLimits(cpu_time=-1)

    def test_negative_memory_mb_raises(self):
        with pytest.raises(ValueError, match="memory_mb.*positive"):
            ResourceLimits(memory_mb=-512)

    def test_negative_disk_mb_raises(self):
        with pytest.raises(ValueError, match="disk_mb.*positive"):
            ResourceLimits(disk_mb=-100)

    def test_zero_values_raise(self):
        with pytest.raises(ValueError, match="positive"):
            ResourceLimits(cpu_time=0)

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError, match="numeric"):
            ResourceLimits(cpu_time="sixty")  # type: ignore

    def test_float_values_converted_to_int(self):
        limits = ResourceLimits(cpu_time=30.5, memory_mb=512.0, disk_mb=100.9)
        assert limits.cpu_time == 30
        assert limits.memory_mb == 512
        assert limits.disk_mb == 100


class TestConfigToSandboxBoundary:
    """Regression tests for Issue #5: config-to-sandbox boundary validation."""

    def test_config_negative_cpu_time_rejected(self, tmp_path):
        """Config with negative sandbox.cpu_time must be rejected at the boundary."""
        from src.common.config import Config
        config_file = tmp_path / "config.json"
        config_file.write_text('{"sandbox": {"cpu_time": -1, "memory_mb": 512, "disk_mb": 100}}')
        config = Config(str(config_file))
        with pytest.raises(ValueError, match="cpu_time.*positive"):
            ResourceLimits(
                cpu_time=config.get("sandbox.cpu_time"),
                memory_mb=config.get("sandbox.memory_mb", 512),
                disk_mb=config.get("sandbox.disk_mb", 100),
            )

    def test_config_negative_memory_mb_rejected(self, tmp_path):
        """Config with negative sandbox.memory_mb must be rejected at the boundary."""
        from src.common.config import Config
        config_file = tmp_path / "config.json"
        config_file.write_text('{"sandbox": {"cpu_time": 60, "memory_mb": -256, "disk_mb": 100}}')
        config = Config(str(config_file))
        with pytest.raises(ValueError, match="memory_mb.*positive"):
            ResourceLimits(
                cpu_time=config.get("sandbox.cpu_time", 60),
                memory_mb=config.get("sandbox.memory_mb"),
                disk_mb=config.get("sandbox.disk_mb", 100),
            )

    def test_config_positive_values_accepted(self, tmp_path):
        """Valid config values must flow through to ResourceLimits."""
        from src.common.config import Config
        config_file = tmp_path / "config.json"
        config_file.write_text('{"sandbox": {"cpu_time": 30, "memory_mb": 1024, "disk_mb": 200}}')
        config = Config(str(config_file))
        limits = ResourceLimits(
            cpu_time=config.get("sandbox.cpu_time"),
            memory_mb=config.get("sandbox.memory_mb"),
            disk_mb=config.get("sandbox.disk_mb"),
        )
        assert limits.cpu_time == 30
        assert limits.memory_mb == 1024
        assert limits.disk_mb == 200

    def test_env_override_negative_rejected(self, tmp_path, monkeypatch):
        """Env override with negative value must be rejected at ResourceLimits boundary."""
        from src.common.config import Config
        config_file = tmp_path / "config.json"
        config_file.write_text('{"sandbox": {"cpu_time": 60}}')
        monkeypatch.setenv("AO_SANDBOX_CPUTIME", "-10")
        config = Config(str(config_file))
        raw = config.get("sandbox.cputime")
        assert raw == "-10"
        # Env values are strings; ResourceLimits rejects non-numeric first
        with pytest.raises(ValueError, match="numeric"):
            ResourceLimits(cpu_time=raw)
