"""Regressions for owned processes, locked vaults and compact terminal decisions."""

import asyncio
import json
import re
import sys
import threading
import xml.etree.ElementTree as ET

import httpx
import pytest
from test_tui import UIModel, run_ui
from textual.widgets import Button, Checkbox, TextArea

from codaro.agent import Agent
from codaro.jobs import JobManager
from codaro.llm import RequestCancelled
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.dialogs import ProviderManager
from codaro.ui.jobs import JobsDialog
from codaro.ui.plan import ExecutionPlan
from codaro.ui.providers import ModelPicker, ProviderSetup
from codaro.ui.reviews import CommandReview, ScopeReview


def visible(app):
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


async def wait_for(predicate, pilot):
    for _ in range(150):
        if predicate():
            return
        await pilot.pause(0.02)
    raise AssertionError("Estado esperado não chegou.")


def planned_agent(root):
    agent = Agent(Repository(root), UIModel(), mode="execute")
    agent.tasks.start("Preservar contrato")
    agent.tasks.plan(
        [{"title": "Implementar", "state": "doing"}, {"title": "Validar", "state": "todo"}],
        ["Testes aprovados"],
    )
    return agent


def test_cancel_plan_stops_owned_descendants_and_preserves_output_and_draft(tmp_path):
    heartbeat = tmp_path / "heartbeat.txt"
    child = (
        'import time; from pathlib import Path; p=Path("heartbeat.txt");\n'
        "while True: p.write_text(str(time.time())); time.sleep(.03)"
    )
    parent = (
        f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); '
        'print("ready",flush=True); time.sleep(30)'
    )
    agent = planned_agent(tmp_path)
    job = agent.jobs.start(
        [sys.executable, "-u", "-c", parent], purpose="validation", task=agent.tasks.current()
    )
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(Prompt).value = "Rascunho preservado"
            await wait_for(lambda: heartbeat.exists(), pilot)
            await wait_for(lambda: app.query_one("#job-strip").display, pilot)
            await pilot.click("#cancel-plan")
            await wait_for(lambda: agent.jobs.jobs[job["job_id"]].done.is_set(), pilot)
            assert agent.tasks.current()["state"] == "cancelled"
            assert agent.jobs.poll(job["job_id"])["state"] == "cancelled"
            assert "ready" in agent.jobs.poll(job["job_id"])["output"]
            old = heartbeat.read_bytes()
            await asyncio.sleep(0.15)
            assert heartbeat.read_bytes() == old
            assert app.query_one(Prompt).value == "Rascunho preservado"
            await wait_for(lambda: not app.query_one("#job-strip").display, pilot)

    try:
        run_ui(scenario())
    finally:
        agent.close()


def test_cancel_while_approval_pending_does_not_approve_or_leave_job(tmp_path):
    agent = planned_agent(tmp_path)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test() as pilot:
            app.busy = True
            job = agent.jobs.start(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    'import time; print("ready",flush=True); time.sleep(30)',
                ]
            )
            decision = asyncio.create_task(
                asyncio.to_thread(app.approve_command, ["not-executed"], 60, app.cancelled)
            )
            await wait_for(lambda: isinstance(app.screen, CommandReview), pilot)
            await pilot.press("ctrl+x")
            assert await asyncio.wait_for(decision, 3) is False
            await wait_for(lambda: agent.jobs.jobs[job["job_id"]].done.is_set(), pilot)
            assert agent.jobs.poll(job["job_id"])["state"] == "cancelled"

    run_ui(scenario())


def test_cancelled_launch_cannot_create_a_late_job(tmp_path):
    manager = JobManager(Repository(tmp_path))
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(RequestCancelled):
        manager.start([sys.executable, "-c", "raise RuntimeError()"], cancelled=stopped)
    assert not manager.jobs


def test_jobs_output_is_paginated_and_user_can_stop_without_a_model_call(tmp_path):
    agent = planned_agent(tmp_path)
    app = CodaroApp(agent)
    job = agent.jobs.start(
        [
            sys.executable,
            "-u",
            "-c",
            'import time; print("A"*4500); print("SECOND_PAGE"); time.sleep(30)',
        ]
    )

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            await wait_for(
                lambda: "SECOND_PAGE" in agent.jobs.poll(job["job_id"], limit=8000)["output"], pilot
            )
            await app.local_command("/jobs")
            await pilot.pause()
            assert isinstance(app.screen, JobsDialog)
            assert "SECOND_PAGE" not in app.screen.query_one(TextArea).text
            await pilot.click("#job-next")
            await pilot.pause()
            assert "SECOND_PAGE" in app.screen.query_one(TextArea).text
            await pilot.click("#job-stop")
            await wait_for(lambda: agent.jobs.jobs[job["job_id"]].done.is_set(), pilot)
            assert not agent.provider.started.is_set()
            await pilot.press("escape")

    run_ui(scenario())


class LockedVault:
    def __init__(self):
        self.reads = 0

    def get(self, reference):
        self.reads += 1
        raise ValueError("Cofre bloqueado · desbloqueie e tente novamente")


def locked_store(root):
    vault = LockedVault()
    store = ProviderStore(
        root / "profiles",
        vault=vault,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [{"id": "demo"}]})
        ),
    )
    store.register("openai", "synthetic-key", name="locked")
    value = store.load(resolve_secrets=False)
    value["version"] = 2
    value["profiles"]["locked"].update(api_key="", credential_ref="a" * 32)
    store.path.write_text(json.dumps(value))
    store.path.chmod(0o600)
    return store, vault


def test_edit_locked_vault_is_recoverable_and_does_not_read_on_render(tmp_path, monkeypatch):
    store, vault = locked_store(tmp_path)
    before = store.path.read_bytes()
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    project = tmp_path / "project"
    project.mkdir()
    app = CodaroApp(Agent(Repository(project), UIModel()))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(Prompt).value = "Meu rascunho"
            app.push_screen(ProviderManager(store), app.manage_provider_decision)
            await pilot.pause()
            await pilot.click("#profile-edit-locked")
            await pilot.pause()
            assert isinstance(app.screen, ProviderSetup) and vault.reads == 0
            assert not app.screen.query_one("#provider-test", Button).disabled
            await app.screen.test_connection()
            assert vault.reads == 1 and "Cofre bloqueado" in visible(app)
            assert app._exception is None and store.path.read_bytes() == before
            await pilot.press("escape")
            assert app.query_one(Prompt).value == "Meu rascunho"
            app.push_screen(ModelPicker(store))
            await pilot.pause()
            assert vault.reads == 1 and app._exception is None

    run_ui(scenario())


@pytest.mark.parametrize(
    "base_url,insecure,expected",
    [
        ("http://localhost:11434", False, "HTTP"),
        ("https://example.test/v1", False, "HTTPS · verificação habilitada"),
        ("https://example.test/v1", True, "HTTPS · sem verificar certificado"),
    ],
)
def test_transport_label_reports_policy_instead_of_claiming_a_verified_connection(
    tmp_path, base_url, insecure, expected
):
    store = ProviderStore(tmp_path / "profiles")
    store.mutate(
        lambda data: data["profiles"].update(
            local={
                "kind": "openai-compatible",
                "base_url": base_url,
                "api_key": "",
                "tls_insecure": insecure,
                "model": "",
                "models": [],
            }
        )
    )
    project = tmp_path / "project"
    project.mkdir()
    app = CodaroApp(Agent(Repository(project), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            app.push_screen(ProviderManager(store))
            await pilot.pause()
            assert expected in visible(app) and "TLS verificado" not in visible(app)
            assert app.screen.query_one("#provider-manager").region.height < 20

    run_ui(scenario())


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35)])
def test_questions_keep_chat_readable_show_distinct_endings_and_restore_plan(tmp_path, size):
    agent = planned_agent(tmp_path)
    app = CodaroApp(agent)
    answers = []
    choices = [
        "Preservar consumidores e compatibilidade. " * 5 + ending
        for ending in ("DESTINO_A", "DESTINO_B", "DESTINO_C")
    ]

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.active_question = "Explique o contrato"
            app.finish("Mensagem curta que deve permanecer legível.")
            app.show_plan()
            plan = app.query_one(ExecutionPlan)
            app.show_user_question(
                {
                    "question": "Qual contrato devemos preservar para a API pública? " * 4,
                    "choices": choices,
                    "recommended": 0,
                },
                answers.append,
            )
            await pilot.pause()
            text = visible(app)
            assert all(ending in text for ending in ["DESTINO_A", "DESTINO_B", "DESTINO_C"])
            assert app.query_one("#conversation").region.height >= 4
            assert plan.expanded
            if size[1] < 25:
                assert not app.query_one("#plan-steps").display
            await pilot.press("down", "enter")
            assert answers == [choices[1]]
            await pilot.pause()
            assert app.query_one("#plan-steps").display and plan.expanded

    run_ui(scenario())


def test_scope_review_keeps_complete_commands_and_safe_cancel_in_small_terminal(tmp_path):
    agent = planned_agent(tmp_path)
    app = CodaroApp(agent)
    decisions = []

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            app.push_screen(ScopeReview(agent.tasks.current()), decisions.append)
            await pilot.pause()
            assert app.focused.id == "cancel-scope"
            assert "2 caminhos · 0 comandos" in visible(app)
            field = app.screen.query_one("#scope-commands", TextArea)
            field.focus()
            field.text = 'python -m pytest "tests/test with spaces.py"'
            await pilot.pause()
            assert field.region.height >= 3 and "1 comandos" in visible(app)
            await pilot.click("#cancel-scope")
            await pilot.pause()
            assert decisions == [None] and agent.policy.kind == "action"

    run_ui(scenario())


def luminance(hex_color):
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
    return sum(x * weight for x, weight in zip(linear, [0.2126, 0.7152, 0.0722], strict=True))


@pytest.mark.parametrize("theme,background", [("codaro", "#111821"), ("textual-light", "#e0e0e0")])
def test_input_placeholder_contrast_in_rendered_theme(tmp_path, monkeypatch, theme, background):
    monkeypatch.delenv("NO_COLOR", raising=False)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            app.theme = theme
            await pilot.pause()
            svg = app.export_screenshot()
            fills = dict(re.findall(r"\.(terminal-[\w-]+)\s*\{\s*fill:\s*(#[0-9a-fA-F]+)", svg))
            nodes = ET.fromstring(svg)
            colors = [
                fills[node.get("class")]
                for node in nodes.iter()
                if node.tag.endswith("text") and "mudança" in "".join(node.itertext())
            ]
            assert colors
            for color in colors:
                a, b = sorted([luminance(color), luminance(background)])
                assert (b + 0.05) / (a + 0.05) >= 4.5

    run_ui(scenario())


def test_cancel_during_question_keeps_it_unanswered_and_stops_commands(tmp_path):
    agent = planned_agent(tmp_path)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            app.busy = True
            job = agent.jobs.start([sys.executable, "-c", "import time; time.sleep(30)"])
            question = asyncio.create_task(
                asyncio.to_thread(
                    app.ask_user_question,
                    {"question": "Manter contrato?", "choices": ["Sim", "Não"]},
                    app.cancelled,
                )
            )
            await wait_for(lambda: app.pending_user_question is not None, pilot)
            await pilot.press("ctrl+x")
            from codaro.agent import InvestigationCancelled

            with pytest.raises(InvestigationCancelled):
                await asyncio.wait_for(question, 3)
            await wait_for(lambda: agent.jobs.jobs[job["job_id"]].done.is_set(), pilot)
            assert not app.current_decisions
            assert agent.jobs.poll(job["job_id"])["state"] == "cancelled"

    run_ui(scenario())


def test_plan_can_be_expanded_during_question_and_keeps_user_preference(tmp_path):
    app = CodaroApp(planned_agent(tmp_path))

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            plan = app.query_one(ExecutionPlan)
            plan.toggle()
            app.show_plan()
            assert not plan.expanded
            app.show_user_question(
                {"question": "Qual API?", "choices": ["Atual", "Nova"]}, lambda _: None
            )
            await pilot.pause()
            await pilot.click("#toggle-plan")
            await pilot.pause()
            assert plan.expanded and app.query_one("#plan-steps").display
            app.clear_user_question()
            await pilot.pause()
            assert plan.expanded and app.query_one("#plan-steps").display

    run_ui(scenario())


def test_advanced_scope_summary_describes_json_that_will_be_authorized(tmp_path):
    app = CodaroApp(planned_agent(tmp_path))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ScopeReview(app.agent.tasks.current()))
            await pilot.pause()
            app.screen.query_one("#scope-advanced", Checkbox).value = True
            await pilot.pause()
            app.screen.query_one("#scope-json", TextArea).text = json.dumps(
                {"paths": ["src"], "commands": [["python", "-m", "pytest"], ["npm", "test"]]}
            )
            await pilot.pause()
            assert "1 caminhos · 2 comandos" in visible(app)
            app.screen.query_one("#scope-json", TextArea).text = "{"
            await pilot.pause()
            assert "JSON em edição" in visible(app)

    run_ui(scenario())
