"""Session, mode and provider transitions; public methods remain on Agent."""

from codaro.context import TokenCounter
from codaro.memory import ConversationMemory
from codaro.policies import Mode
from codaro.tasks import TaskStore


def reset_calibration(agent):
    if agent._lock.locked() or agent.edits.pending:
        raise ValueError("Conclua/cancele a ação atual antes de recalibrar.")
    agent.memory.reset_calibration()
    if agent._original_settings is not None:
        agent.provider.settings = agent._original_settings
        agent.context_window = agent._original_settings.context_window
        agent.max_output_tokens = agent._original_settings.output_reserve
    agent.input_limit = agent.context_window - agent.max_output_tokens - 512
    agent.adaptive_input_limit = agent.input_limit
    agent.local_read_budget = min(6000, max(1200, agent.input_limit // 3))
    if agent._configured_context_budget is None:
        agent.context_budget = max(64_000, min(512_000, agent.context_window * 3))
    agent.counter.scale, agent.counter.samples = 1.0, []
    agent._calibration_key = ""


def set_mode(agent, mode):
    if agent._lock.locked() or agent.edits.pending:
        raise ValueError("Conclua/cancele a ação atual antes de trocar de modo.")
    agent.mode = Mode(mode)
    agent.allow_edits = agent.mode == Mode.EXECUTE
    agent._pending_text_continuation = False
    agent.policy.reset()
    agent.max_steps = (
        agent._configured_steps
        if agent._configured_steps is not None
        else 8
        if agent.mode == Mode.ASK
        else 20
        if agent.mode == Mode.PLAN
        else 32
    )
    agent.tool_budget = (
        agent._configured_tool_budget
        if agent._configured_tool_budget is not None
        else 24_000
        if agent.mode == Mode.ASK
        else 96_000
    )


def set_provider(agent, provider):
    if agent._lock.locked() or agent.edits.pending:
        raise ValueError("Conclua/cancele a ação atual antes de trocar de provedor/modelo.")
    agent.provider = provider
    settings = provider.settings
    agent._original_settings = settings
    agent.context_window, agent.max_output_tokens = (
        settings.context_window,
        settings.output_reserve,
    )
    if agent._configured_context_budget is None:
        agent.context_budget = max(64_000, min(512_000, agent.context_window * 3))
    agent.input_limit = agent.context_window - agent.max_output_tokens - 512
    agent.adaptive_input_limit = agent.input_limit
    agent.local_read_budget = min(6000, max(1200, agent.input_limit // 3))
    agent.counter = TokenCounter(settings.token_encoding)
    agent.context.counter = agent.counter
    agent.artifacts.redact = agent.memory.redact
    agent._calibration_key = ""
    from codaro.sessions import SessionStore

    previous, current = (
        agent.memory.redact,
        SessionStore(agent.repository.root, settings.api_key).redact,
    )
    agent.memory.redact = lambda value: current(previous(value))
    agent.tasks.redact = agent.memory.redact
    agent.turns = agent.memory.redact(agent.turns)
    agent.artifacts.redact = agent.memory.redact
    agent.policy.reset()


def activate_session(agent, identifier):
    if agent._lock.locked() or agent.edits.pending:
        raise ValueError("Aguarde a execução e revise propostas antes de trocar sessão.")
    agent._pending_text_continuation = False
    store = agent.sessions.store(identifier)
    try:
        turns = store.load()
    except FileNotFoundError:
        turns = []
    summary = agent.sessions.summary(identifier)
    agent.sessions.activate(identifier)
    old_redact = agent.memory.redact
    agent.memory = ConversationMemory(
        agent.repository.root,
        getattr(getattr(agent.provider, "settings", None), "api_key", ""),
        ephemeral=agent.tasks.ephemeral,
    )
    agent.memory.redact = old_redact
    agent.tasks = TaskStore(
        agent.repository.root, ephemeral=agent.tasks.ephemeral, redact=old_redact
    )
    if identifier != "default":
        agent.memory.path = agent.repository.root / (".codaro/memory-" + identifier + ".sqlite3")
        agent.tasks.path = agent.repository.root / (".codaro/tasks-" + identifier + ".json")
    agent.edits.checkpoints.session_id = identifier
    agent.session_id = agent.artifacts.session_id = identifier
    agent.artifacts.redact = old_redact
    agent.turns = turns
    agent.context.summary = summary
    agent.policy.reset()
    agent.edits.observed.clear()
    agent.edits.proposals.clear()
    return store
