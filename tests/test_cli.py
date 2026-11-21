import json
import os

from typer.testing import CliRunner

from dungscraper import cli

runner = CliRunner()


def test_chunk_prints_unicode_unescaped(tmp_path):
    path = tmp_path / "lt.md"
    path.write_text("# Šiandien ąčęėįšųū žemėlapis\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["chunk", str(path)])
    assert result.exit_code == 0
    assert "Šiandien ąčęėįšųū" in result.output
    assert json.loads(result.output)[0]["index"] == 0


def test_cli_commands_default_to_quiet_logs(monkeypatch, tmp_path):
    monkeypatch.delenv("DUNGSCRAPER_LOG_LEVEL", raising=False)
    path = tmp_path / "a.md"
    path.write_text("hello\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["chunk", str(path)])
    assert result.exit_code == 0
    assert os.environ["DUNGSCRAPER_LOG_LEVEL"] == "WARNING"
    monkeypatch.delenv("DUNGSCRAPER_LOG_LEVEL", raising=False)


def test_log_level_option_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("DUNGSCRAPER_LOG_LEVEL", "WARNING")
    path = tmp_path / "a.md"
    path.write_text("hello\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["--log-level", "debug", "chunk", str(path)])
    assert result.exit_code == 0
    assert os.environ["DUNGSCRAPER_LOG_LEVEL"] == "DEBUG"


def test_missing_file_is_a_clean_error(tmp_path):
    result = runner.invoke(cli.app, ["chunk", str(tmp_path / "missing.md")])
    assert result.exit_code == 1
