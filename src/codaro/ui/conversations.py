"""Conversation rendering and navigation, backed by the shared conversation writer."""

import copy

from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from codaro.index import safe_preview
from codaro.interaction import InputHistory
from codaro.llm import ModelError
from codaro.ui.composer import Prompt
from codaro.ui.messages import AssistantMessage, UserMessage
from codaro.ui.widgets import ProposalCard


def save_session(self):
    try:
        self.agent.conversation.replace(
            self.session_turns,
            self.agent.provider.settings.model,
            decisions=self.session_decisions,
            pending_decisions=self.current_decisions,
        )
        return True
    except (ValueError, OSError, ModelError) as exc:
        self.mount_message(
            Static(
                safe_preview(f"Não foi possível salvar a sessão: {exc}"),
                classes="notice",
                markup=False,
            )
        )
        return False


async def restore_session(self):
    try:
        turns = self.session.load()
    except (ValueError, OSError, ModelError) as exc:
        self.mount_message(
            Static(safe_preview(f"Não foi possível retomar: {exc}"), classes="notice", markup=False)
        )
        return
    if not turns and not self.session.pending_decisions:
        self.mount_message(Static("Nenhuma conversa salva neste projeto.", classes="notice"))
        return
    self.session_turns = turns
    self.session_decisions = copy.deepcopy(self.session.decisions)
    self.current_decisions = copy.deepcopy(self.session.pending_decisions)
    self.agent.turns = list(turns)
    self.generation_rate.reset()
    self.update_generation_status()
    self.agent.conversation.index_decisions(self.agent.memory, self.agent.provider.settings.model)
    self.history = InputHistory(turn[0]["content"] for turn in turns)
    self.reply = self.speaker = None
    self.response_text = self.rendered_text = ""
    self.activity_group = None
    self.reasoning_group = None
    conversation = self.query_one("#conversation", VerticalScroll)
    await conversation.remove_children()
    for index, turn in enumerate(turns[-30:], max(0, len(turns) - 30)):
        self.mount_message(UserMessage(turn[0]["content"]))
        for decision in self.session_decisions[index]:
            self.mount_decision(decision)
        self.mount_message(Static("Codaro", classes="speaker", markup=False))
        self.mount_message(AssistantMessage(safe_preview(turn[-1]["content"])))
    for decision in self.current_decisions:
        self.mount_decision(decision)
    self.mount_message(
        Static(
            "Sessão retomada. Revise os arquivos atuais; propostas anteriores não são reaplicadas.",
            classes="notice",
            markup=False,
        )
    )
    self.query_one("#status", Static).update("Pronto · sessão retomada")


async def action_clear_chat(self):
    if self.busy or isinstance(self.screen, ModalScreen):
        return
    self.clear_backup = (
        copy.deepcopy(self.agent.turns),
        copy.deepcopy(self.session_turns),
        copy.deepcopy(self.session_decisions),
        copy.deepcopy(self.current_decisions),
    )
    self.agent.turns.clear()
    self.session_turns.clear()
    self.session_decisions = []
    self.current_decisions = []
    self.generation_rate.reset()
    self.update_generation_status()
    self.response_text = self.rendered_text = ""
    self.reply = self.speaker = None
    self.activity_group = None
    self.reasoning_group = None
    self.pending_recovery = self.retry_recovery = None
    self.answer_preview = self.generation_preview = self.reasoning_preview = None
    conversation = self.query_one("#conversation", VerticalScroll)
    await conversation.remove_children()
    await conversation.mount(self.welcome())
    self.proposal_cards.clear()
    for proposal in self.agent.edits.pending:
        card = ProposalCard(proposal)
        self.proposal_cards[proposal.id] = card
        self.mount_message(card)
    self.show_plan()
    self.update_session_header()
    self.save_session()
    self.query_one(Prompt).focus()
    self.query_one("#new-messages").display = False
    self.query_one("#status", Static).update(
        "Mensagens limpas · tarefa, escopo e propostas mantidos · /restore-clear desfaz"
    )


async def restore_clear(self):
    if self.clear_backup is None:
        self.mount_message(Static("Não há limpeza para desfazer.", classes="notice"))
        return
    self.agent.turns, self.session_turns, self.session_decisions, self.current_decisions = (
        self.clear_backup
    )
    self.clear_backup = None
    self.save_session()
    await self.restore_session()
    self.proposal_cards.clear()
    for proposal in self.agent.edits.pending:
        card = ProposalCard(proposal)
        self.proposal_cards[proposal.id] = card
        self.mount_message(card)
    self.show_plan()


async def reset_conversation(self, objective):
    if self.busy:
        return
    try:
        previous_tasks, previous_memory = self.agent.tasks, self.agent.memory
        previous = previous_tasks.current()
        if not self.save_session():
            return
        # Activation prepares and validates the destination before touching
        # the current task, proposals, grants or pending question.
        from codaro.agent.lifecycle import new_session

        self.session = new_session(self.agent)
        self.clear_user_question()
        cleanup_error = None
        try:
            if previous and previous["state"] not in {"completed", "cancelled"}:
                previous_tasks.state("cancelled", "Encerrado ao começar outra conversa.")
            previous_tasks.mutate(lambda data: data.update(active=None))
            previous_memory.clear_task()
        except (ValueError, OSError) as exc:
            # The new session is already committed. Keep the UI attached to it
            # even if closing the previous task cannot be persisted.
            cleanup_error = exc
        self.recorded_completions = self.agent.conversation.completed
        self.session.secret = self.agent.provider.settings.api_key
        self.session_turns = []
        self.session_decisions = []
        self.current_decisions = []
        self.next_mode = None
        self.query_one(Prompt).value = ""
        await self.action_clear_chat()
        self.clear_backup = None
        self.history = InputHistory()
        if objective:
            self.agent.tasks.start(objective, new=True)
        self.update_session_header()
        self.query_one("#status", Static).update(
            "Nova conversa · permissões por ação · propostas anteriores descartadas"
        )
        if cleanup_error:
            self.mount_message(
                Static(
                    safe_preview(
                        "Nova conversa aberta; não foi possível encerrar a tarefa anterior: "
                        + str(cleanup_error)
                    ),
                    classes="notice",
                    markup=False,
                )
            )
    except (ValueError, OSError, ModelError) as exc:
        self.mount_message(
            Static(
                safe_preview(f"Não foi possível iniciar outra conversa: {exc}"),
                classes="notice",
                markup=False,
            )
        )
