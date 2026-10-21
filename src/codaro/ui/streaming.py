"""Streaming and activity rendering, separate from application lifecycle and commands."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from codaro.agent import AgentEvent
from codaro.index import safe_preview
from codaro.llm import MAX_MESSAGE_CHARS
from codaro.ui.widgets import ActivityGroup, GenerationPreview, ReasoningGroup


def activity(app, event: AgentEvent):
    if event.kind == "model_start":
        tokens = event.context_tokens or 0
        limit = event.context_limit or app.agent.adaptive_input_limit
        capacity = f"{limit / 1000:.1f}k" if limit >= 1000 else str(limit)
        meter = app.query_one("#context-meter", Static)
        meter.update(f"Contexto ≈ {round(tokens * 100 / max(1, limit))}% · {capacity} · /status")
        meter.tooltip = (
            f"Enviado: ≈ {tokens:,}/{limit:,} tokens · {app.agent.provider.settings.context_source}"
        )
    elif event.kind == "compaction" or event.title in {
        "Contexto liberado",
        "Contexto reduzido",
    }:
        app.query_one("#context-meter", Static).update(
            "Contexto compactado automaticamente · /status"
        )
    if event.kind == "plan":
        app.show_plan()
    elif event.kind == "model_start":
        app.update_session_header()
        app.finish_preview("retry")
        if app.answer_preview is not None:
            app.answer_preview.finish("retry")
            app.answer_preview = None
        app.context_chars = event.context_chars or 0
        app.context_tokens = event.context_tokens or 0
        app.reported_tokens = None
        app.context_limit = event.context_limit or app.agent.adaptive_input_limit
        app.set_generation_status("Investigando…")

    elif event.reported_tokens is not None:
        app.reported_tokens = event.reported_tokens
        app.set_generation_status("Respondendo…")
    elif event.kind == "model_end":
        app.finish_preview(
            event.state,
            " · ".join(filter(None, (event.title, event.detail))) if event.state == "retry" else "",
        )
        if event.state == "retry" and app.answer_preview is not None:
            app.answer_preview.finish("retry", event.title)
            app.answer_preview = None
    elif event.kind == "tool_start":
        app.set_generation_status(safe_preview(f"{event.title}… · {event.detail}".rstrip(" ·")))
    elif event.kind == "tool_end":
        app.show_plan()
        duration = f"{event.elapsed_ms:.0f} ms" if event.elapsed_ms is not None else ""
        if app.activity_group is None:
            app.activity_group = ActivityGroup()
            app.mount_message(app.activity_group)
        app.activity_group.add(event)
        app.set_generation_status(f"{event.title} · {duration}")
    else:
        app.set_generation_status("Investigando…")


def append_delta(app, delta: str):
    # A content delta may precede native tool_calls in the same message.
    # Only finish() can commit an answer to the conversation.
    app.draft_text = (app.draft_text + safe_preview(delta))[:MAX_MESSAGE_CHARS]
    app.set_generation_status("Respondendo…")
    # Render the first fragment immediately; subsequent fragments are coalesced by the timer.
    if app.generation_preview is None:
        app.flush_preview()


def append_reasoning(app, delta: str):
    if app.reasoning_preview is None:
        app.reasoning_preview = GenerationPreview(reasoning=True)
        app.reasoning_preview.collapsed = False
        if app.reasoning_group is None:
            app.reasoning_group = ReasoningGroup(
                app.reasoning_preview, collapsed=not app.show_reasoning
            )
            app.mount_message(app.reasoning_group)
        else:
            app.reasoning_group.add_preview(app.reasoning_preview)
    app.reasoning_preview.update_text((app.reasoning_preview.text + safe_preview(delta))[:4001])
    app.set_generation_status("Investigando…")


def flush_preview(app):
    if not app.draft_text or app.draft_text == app.draft_rendered:
        return
    conversation = app.query_one("#conversation", VerticalScroll)
    follow = conversation.is_vertical_scroll_end
    if app.generation_preview is None:
        app.generation_preview = GenerationPreview()
        if app.activity_group is None:
            app.activity_group = ActivityGroup()
            app.mount_message(app.activity_group)
        app.activity_group.add_preview(app.generation_preview)
        app.mount_message(app.generation_preview)
    app.generation_preview.update_text(app.draft_text)
    app.draft_rendered = app.draft_text
    if follow:
        app.call_after_refresh(conversation.scroll_end, animate=False)


def finish_preview(app, state: str, reason: str = ""):
    app.flush_preview()
    if app.generation_preview is not None:
        app.generation_preview.finish(state, reason)
        if state == "answer":
            app.answer_preview = app.generation_preview
    if app.reasoning_preview is not None:
        app.reasoning_preview.finish(state)
        app.reasoning_preview = None
    app.draft_text = app.draft_rendered = ""
    app.generation_preview = None


def flush_response(app):
    if not app.response_text or app.response_text == app.rendered_text:
        return
    conversation = app.query_one("#conversation", VerticalScroll)
    follow = conversation.is_vertical_scroll_end
    if app.reply is None:
        app.speaker = Static("Codaro", classes="speaker", markup=False)
        app.mount_message(app.speaker)
        app.reply = Markdown(app.response_text, classes="assistant", open_links=False)
        app.mount_message(app.reply)
    else:
        app.reply.update(app.response_text)
    app.rendered_text = app.response_text
    if follow:
        app.call_after_refresh(conversation.scroll_end, animate=False)


def discard_response(app):
    app.response_text = app.rendered_text = ""
    if app.reply is not None:
        app.reply.remove()
        app.reply = None
    if app.speaker is not None:
        app.speaker.remove()
        app.speaker = None
