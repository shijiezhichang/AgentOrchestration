import pytest
import sys
from io import StringIO
from src.cli.main import cli, VALID_OUTPUT_MODES


class TestCLIOutputModeValidation:
    """Regression tests for CLI output mode validation (Issue #6)."""

    def test_valid_output_mode_text(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["status", "--output", "text"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        output = out.getvalue()
        assert "agents" in output or "3" in output

    def test_valid_output_mode_json(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["status", "--output", "json"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        output = out.getvalue()
        assert '"agents"' in output
        assert '"running"' in output

    def test_valid_output_mode_table(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["status", "--output", "table"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        output = out.getvalue()
        assert "agents" in output

    def test_valid_output_mode_short_flag(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["status", "-o", "json"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        assert '"agents"' in out.getvalue()

    def test_unsupported_output_mode_rejected(self):
        with pytest.raises(SystemExit) as exc:
            cli(["status", "--output", "xml"])
        assert exc.value.code != 0

    def test_unsupported_output_mode_rejected_short(self):
        with pytest.raises(SystemExit) as exc:
            cli(["status", "-o", "yaml"])
        assert exc.value.code != 0

    def test_unsupported_output_mode_rejected_empty(self):
        with pytest.raises(SystemExit) as exc:
            cli(["status", "--output", ""])  # empty not in choices
        assert exc.value.code != 0

    def test_default_output_is_text(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["init", "myproj"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        output = out.getvalue()
        assert "myproj" in output
        assert '"project"' not in output  # not JSON by default

    def test_json_output_for_deploy(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["deploy", "manifest.yaml", "--output", "json"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        assert '"manifest"' in out.getvalue()
        assert '"deployed"' in out.getvalue()

    def test_table_output_for_logs(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["logs", "agent-123", "--output", "table"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        assert "agent-123" in out.getvalue()

    def test_all_output_modes_listed_in_help(self):
        out = StringIO()
        sys.stdout = out
        try:
            cli(["--help"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        helptext = out.getvalue()
        for mode in VALID_OUTPUT_MODES:
            assert mode in helptext, f"{mode} not in help text"

    def test_invalid_mode_shows_error_message(self):
        out = StringIO()
        sys.stderr = out
        try:
            cli(["status", "--output", "pdf"])
        except SystemExit:
            pass
        finally:
            sys.stderr = sys.__stderr__
        err = out.getvalue()
        # argparse should mention invalid choice
        assert "invalid" in err.lower() or "choice" in err.lower()
