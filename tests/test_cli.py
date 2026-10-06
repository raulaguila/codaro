import json

from rich.text import Text
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


def install_edit_model(monkeypatch):
    from test_agent import edit_responses

    responses = iter(edit_responses())

    def stream(self, messages, tools=None, on_delta=None, cancelled=None):
        response = next(responses)
        if response.get("content"):
            on_delta(response["content"])
        return response

    monkeypatch.setattr("codaro.cli.OpenAICompatible.stream", stream)


def test_edit_cli_approval_applies_only_after_diff(tmp_path, monkeypatch):
    (tmp_path / "code.py").write_text("x = 1\n")
    install_edit_model(monkeypatch)
    result = runner.invoke(app, ["edit", "Mude x.", "--repo", str(tmp_path)], input="y\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "code.py").read_text() == "x = 2\n"
    assert "-x = 1" in result.stdout and "+x = 2" in result.stdout
    assert result.stdout.index("+x = 2") < result.stdout.index("Aplicar a edição")


def test_edit_cli_default_rejects(tmp_path, monkeypatch):
    (tmp_path / "code.py").write_text("x = 1\n")
    install_edit_model(monkeypatch)
    result = runner.invoke(app, ["edit", "Mude x.", "--repo", str(tmp_path)], input="\n")
    assert result.exit_code == 0, result.output
    assert "rejeitada" in result.stdout
    assert (tmp_path / "code.py").read_text() == "x = 1\n"


def test_edit_cli_eof_cannot_apply(tmp_path, monkeypatch):
    (tmp_path / "code.py").write_text("x = 1\n")
    install_edit_model(monkeypatch)
    result = runner.invoke(app, ["edit", "Mude x.", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert (tmp_path / "code.py").read_text() == "x = 1\n"


def test_directory_shortcut_uses_terminal_working_directory(tmp_path, monkeypatch):
    captured = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("codaro.tui.CodaroApp.run", lambda self: captured.append(self.agent))
    result = runner.invoke(app, ["."])
    assert result.exit_code == 0, result.output
    assert captured[0].repository.root == tmp_path.resolve()
    assert captured[0].allow_edits


def test_directory_shortcut_accepts_other_paths_and_chat_options(tmp_path, monkeypatch):
    captured = []
    project = tmp_path / "projeto com espaços"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("codaro.tui.CodaroApp.run", lambda self: captured.append(self.agent))
    for target in (project.name, "./" + project.name, str(project)):
        result = runner.invoke(app, [target, "--read-only"])
        assert result.exit_code == 0, result.output
        assert captured[-1].repository.root == project.resolve()
        assert not captured[-1].allow_edits


def test_directory_shortcut_reports_missing_directory(tmp_path):
    result = runner.invoke(app, [str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "Diretório inexistente" in result.stderr
    assert "Traceback" not in result.output


def test_command_names_take_precedence_over_directories(tmp_path, monkeypatch):
    (tmp_path / "doctor").mkdir()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Diagnóstico" in result.stdout


def test_unknown_command_still_reports_typo():
    result = runner.invoke(app, ["serach"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_directory_shortcut_help_does_not_launch_chat():
    result = runner.invoke(app, [".", "--help"])
    assert result.exit_code == 0
    assert "--read-only" in Text.from_ansi(result.stdout).plain


def test_pwd_reports_selected_root_without_provider(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("pwd não deve chamar o modelo")

    monkeypatch.setattr("codaro.cli.OpenAICompatible", unavailable)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["pwd", "--repo", "project"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(project.resolve())
    result = runner.invoke(app, ["pwd"])
    assert result.stdout.strip() == str(tmp_path.resolve())


def test_shortcut_can_read_files_and_exposes_root_to_model(tmp_path, monkeypatch):
    from test_agent import FakeModel, call

    (tmp_path / "current.py").write_text("selected_directory = True\n")
    model = FakeModel(
        [
            call("read_lines", {"path": "current.py", "start": 1, "end": 1}),
            {"content": "Arquivo da pasta atual lido."},
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("codaro.cli.OpenAICompatible", lambda settings: model)
    monkeypatch.setattr("codaro.tui.CodaroApp.run", lambda self: self.agent.ask("Leia current.py."))
    result = runner.invoke(app, ["."])
    assert result.exit_code == 0, result.output
    assert str(tmp_path.resolve()) in model.requests[0][0][0]["content"]
    tool = next(item for item in model.requests[-1][0] if item["role"] == "tool")
    assert "selected_directory = True" in json.loads(tool["content"])["content"]


def test_chat_tls_flag_and_secure_override_reach_settings(tmp_path, monkeypatch):
    captured = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "codaro.tui.CodaroApp.run", lambda self: captured.append(self.agent.provider.settings)
    )
    monkeypatch.delenv("CODARO_TLS_INSECURE", raising=False)
    result = runner.invoke(app, [".", "--tls-insecure"])
    assert result.exit_code == 0, result.output
    assert captured[-1].tls_insecure
    monkeypatch.setenv("CODARO_TLS_INSECURE", "true")
    result = runner.invoke(app, ["chat", "--repo", str(tmp_path), "--tls-verify"])
    assert result.exit_code == 0, result.output
    assert not captured[-1].tls_insecure


def test_tls_flag_applies_to_ask_and_edit(tmp_path, monkeypatch):
    captured = []

    def stream(self, messages, tools=None, on_delta=None, cancelled=None):
        captured.append(self.settings.tls_insecure)
        on_delta("OK")
        return {"content": "OK"}

    monkeypatch.setattr("codaro.cli.OpenAICompatible.stream", stream)
    for command in ("ask", "edit"):
        result = runner.invoke(app, [command, "Teste.", "--repo", str(tmp_path), "--tls-insecure"])
        assert result.exit_code == 0, result.output
    assert captured == [True, True]


def test_doctor_shows_effective_tls_setting(monkeypatch):
    monkeypatch.setenv("CODARO_TLS_INSECURE", "true")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "TLS: sem verificação" in result.stdout
    result = runner.invoke(app, ["doctor", "--tls-verify"])
    assert "TLS: verificação ativa" in result.stdout


def test_doctor_tool_probe_is_explicit_and_supports_tls_flag(monkeypatch):
    captured = []

    def probe(self):
        captured.append(self.settings.tls_insecure)

    monkeypatch.setattr("codaro.cli.OpenAICompatible.check_tool_calling", probe)
    assert runner.invoke(app, ["doctor"]).exit_code == 0
    assert captured == []
    result = runner.invoke(app, ["doctor", "--check-tools", "--tls-insecure"])
    assert result.exit_code == 0, result.output
    assert captured == [True]
    assert "resultado e resposta final confirmados" in result.stdout
