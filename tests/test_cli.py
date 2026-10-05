import json

from typer.testing import CliRunner

from codaro.cli import app

runner = CliRunner()


def test_local_cli_flow(tmp_path):
    (tmp_path / "auth.py").write_text("def can_edit(user):\n    return user.is_admin\n")
    assert runner.invoke(app, ["index", "--repo", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["search", "can_edit", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["results"][0]["symbol"] == "can_edit"
    result = runner.invoke(app, ["read", "auth.py", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "return user.is_admin" in result.stdout


def test_json_output_is_not_wrapped_at_terminal_width(tmp_path):
    name = "a" * 180 + ".py"
    (tmp_path / name).write_text("def symbol(): pass")
    result = runner.invoke(app, ["search", "symbol", "--repo", str(tmp_path), "--json"])
    assert json.loads(result.stdout)["results"][0]["path"] == name


def test_invalid_repo_exits_without_traceback(tmp_path):
    result = runner.invoke(app, ["index", "--repo", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "Diretório inexistente" in result.stderr
    assert "Traceback" not in result.output


def test_corrupt_index_is_reported(tmp_path):
    storage = tmp_path / ".codaro"
    storage.mkdir()
    (storage / "index.sqlite3").write_bytes(b"not a database")
    result = runner.invoke(app, ["index", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert "SQLite" in result.stderr
    assert "Traceback" not in result.output


def test_doctor_handles_invalid_settings_and_never_prints_key(monkeypatch):
    monkeypatch.setenv("CODARO_API_KEY", "do-not-print-this-key")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "do-not-print-this-key" not in result.output
    monkeypatch.setenv("CODARO_BASE_URL", "file:///private")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_bad_read_range_returns_error(tmp_path):
    (tmp_path / "x.py").write_text("x = 1")
    result = runner.invoke(
        app, ["read", "x.py", "--repo", str(tmp_path), "--start", "2", "--end", "1"]
    )
    assert result.exit_code == 1


def test_ask_reports_provider_failure(tmp_path, monkeypatch):
    from codaro.provider import ModelError

    def fail(*args, **kwargs):
        raise ModelError("API indisponível.")

    monkeypatch.setattr("codaro.cli.OpenAICompatible.complete", fail)
    monkeypatch.setattr("codaro.cli.OpenAICompatible.stream", fail)
    result = runner.invoke(app, ["ask", "Explique.", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert "API indisponível" in result.stderr


def test_doctor_fails_when_ripgrep_is_missing(monkeypatch):
    monkeypatch.setattr("codaro.cli.shutil.which", lambda name: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "ausente" in result.stdout


def test_ask_stream_renders_answer_and_tools_without_duplicate_output(tmp_path, monkeypatch):
    def stream(self, messages, tools=None, on_delta=None, cancelled=None):
        on_delta("**Resultado:** ")
        on_delta("resposta final.")
        return {"content": "**Resultado:** resposta final."}

    monkeypatch.setattr("codaro.cli.OpenAICompatible.stream", stream)
    result = runner.invoke(app, ["ask", "Investigue.", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout.count("resposta final.") == 1
