"""Render design mockups from the prototype catalog, not application screenshots."""

import html
import json
import subprocess
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "screens"
OUT.mkdir(exist_ok=True)
DATA = json.loads((ROOT / "screens.json").read_text())
W, H = 1120, 760
BG, PANEL, RAISED = "#111821", "#17212d", "#1d2b38"
FG, MUTED, FAINT = "#dfe6ef", "#94a3b6", "#8392a6"
ACCENT, LINE, GOOD, WARN, BAD = "#83d6d7", "#2a394b", "#8bc6a4", "#e2bd7c", "#e3959c"


class Canvas:
    def __init__(self):
        self.parts = []

    def rect(self, x, y, width, height, fill, stroke=None, radius=0, opacity=1):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'fill="{fill}" rx="{radius}" opacity="{opacity}" '
            f'stroke="{stroke or "none"}"/>'
        )

    def line(self, x, y, width, color=LINE):
        self.rect(x, y, width, 1, color)

    def text(self, x, y, text, color=FG, size=14, bold=False, anchor="start"):
        self.parts.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
            f'font-weight="{600 if bold else 400}" text-anchor="{anchor}">'
            f"{html.escape(str(text))}</text>"
        )

    def paragraph(self, x, y, text, width=92, color=FG, size=14):
        for line in textwrap.wrap(text, width=width):
            self.text(x, y, line, color, size)
            y += 24
        return y

    def button(self, x, y, text, primary=False, width=None):
        width = width or max(84, len(text) * 7.7 + 26)
        self.rect(x, y, width, 32, ACCENT if primary else RAISED, radius=3)
        self.text(x + 13, y + 21, text, "#102024" if primary else FG, 12, primary)
        return width

    def field(self, x, y, label, value, width=568):
        self.text(x, y, label, MUTED, 12)
        self.rect(x, y + 10, width, 34, BG, LINE, 3)
        self.text(x + 12, y + 32, value, FG, 13)
        return y + 70

    def save(self, path, title):
        document = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title)}">'
            f"<title>{html.escape(title)} · maquete de UI</title>"
            '<g font-family="DejaVu Sans Mono,monospace">' + "".join(self.parts) + "</g></svg>"
        )
        path.write_text(document, encoding="utf-8")


def base(c, state):
    c.rect(0, 0, W, H, BG)
    c.rect(0, 0, W, 32, "#141d28")
    c.text(26, 22, "●  ●  ●", FAINT, 10)
    c.text(W / 2, 22, "Codaro · proposta navegável", FAINT, 11, anchor="middle")
    c.text(W - 24, 22, "protótipo", FAINT, 10, anchor="end")
    c.text(30, 72, "◈", ACCENT, 23)
    c.text(60, 72, "Codaro", FG, 18, True)
    c.text(150, 72, "codaro", MUTED, 15)
    c.text(
        W - 160,
        70,
        "Conectar modelo" if state["id"] == "first-run" else "qwen3.8:27b",
        MUTED,
        12,
        anchor="end",
    )
    c.button(W - 140, 48, "Executar ▾", width=110)
    c.line(30, 98, W - 60, "#223142")


def composer(c, state):
    sid = state["id"]
    phases = {
        "plan-collapsed": "Em execução",
        "plan-expanded": "Em execução",
        "plan-chat": "Em execução",
        "thinking": "Analisando · 8s",
        "tool-running": "Consultando arquivos · 8s",
        "stages-expanded": "Analisando · 8s",
        "streaming": "Respondendo · 12s",
        "validating": "Validando · 18s",
        "context-compacting": "Reorganizando contexto",
        "ask-choice": "Aguardando sua resposta",
        "ask-text": "Aguardando sua resposta",
        "context-scope": "Aguardando sua escolha",
        "done": "Concluído",
        "connection-error": "Conexão interrompida",
        "auth-error": "Configuração necessária",
        "cancelled": "Execução interrompida",
        "first-run": "Selecione um provedor",
    }
    busy = sid in {
        "thinking",
        "tool-running",
        "stages-expanded",
        "streaming",
        "validating",
        "context-compacting",
        "plan-collapsed",
        "plan-expanded",
        "plan-chat",
    }
    question = state["kind"] == "question"
    if sid in {
        "plan",
        "plan-collapsed",
        "plan-expanded",
        "plan-chat",
        "review-diff",
        "command-approval",
        "validating",
    }:
        plan = DATA["plans"][sid]
        expanded = sid != "plan-collapsed"
        top = 437 if expanded else 580
        c.line(30, top - 13, W - 60, "#4b858c")
        c.text(
            34,
            top + 8,
            ("▾" if expanded else "▸") + f" Plano · {plan['completed']}/4 concluídas",
            MUTED,
            12,
        )
        c.text(285, top + 8, plan["current"], MUTED, 12)
        c.button(W - 164, top - 12, "Cancelar plano", width=134)
        if expanded:
            for index, step in enumerate(plan["steps"]):
                yy = top + 40 + index * 30
                c.text(
                    50,
                    yy,
                    "✓"
                    if step["status"] == "done"
                    else "◌"
                    if step["status"] == "running"
                    else "○",
                    GOOD
                    if step["status"] == "done"
                    else ACCENT
                    if step["status"] == "running"
                    else FAINT,
                    13,
                )
                c.text(75, yy, step["title"], FG if step["status"] == "running" else MUTED, 12)
    if question:
        q = DATA["questions"][sid]
        top = 426 if q["choices"] else 546
        c.rect(30, top - 12, W - 60, 682 - top + 12, PANEL, "#45616d", 4)
        c.text(48, top + 20, "? Pergunta", MUTED, 11)
        c.text(48, top + 48, q["title"], FG, 15)
        c.paragraph(48, top + 74, q["why"], 117, MUTED, 12)
        for i, option in enumerate(q["choices"]):
            yy = top + 94 + i * 33
            if i == 0:
                c.rect(48, yy, W - 88, 29, RAISED, LINE, 3)
            c.text(59, yy + 20, "◉" if i == 0 else "○", ACCENT if i == 0 else FAINT, 13)
            c.text(82, yy + 20, option, FG, 12)
            if i == q["recommended"]:
                c.text(W - 62, yy + 20, "recomendado", MUTED, 10, anchor="end")
        c.button(W - 224, 635, "Pausar", width=80)
        c.button(W - 134, 635, "Enviar ↵", True, width=84)
    else:
        c.line(30, 620, W - 60, "#4b858c")
    c.text(48 if question else 33, 652, "›", ACCENT, 20)
    draft = {"slash": "/", "slash-filter": "/mo", "slash-empty": "/xyz"}.get(sid)
    placeholder = draft or (
        "Escreva sua resposta…"
        if question
        else "Escreva seu próximo pedido enquanto aguarda…"
        if busy
        else "Pergunte ou peça uma mudança…"
    )
    c.text(71 if question else 58, 651, placeholder, FG if draft else FAINT, 14)
    c.text(30, 709, phases.get(sid, "Pronto"), MUTED, 11)
    if sid not in {"welcome", "first-run", "slash", "slash-filter", "slash-empty"}:
        c.text(W - 30, 709, "Contexto ≈16k/122k · 13%", FAINT, 11, anchor="end")
    help_text = (
        "↑↓ navegar · Enter selecionar · Esc fechar"
        if sid.startswith("slash")
        else "↑↓ escolher · Enter confirmar · ou digite sua resposta · Esc pausar"
        if question
        else "Ctrl+X interromper · rascunho preservado"
        if busy
        else "Enter enviar · / comandos · Alt+Enter nova linha"
    )
    c.text(30, 740, help_text, FAINT, 10)
    c.text(W - 30, 740, "Ctrl+P menu", FAINT, 10, anchor="end")


def user(c, text="Analise a autenticação e proponha melhorias.", y=145):
    c.rect(30, y - 31, W - 60, 54, PANEL, radius=2, opacity=0.6)
    c.rect(30, y - 31, 2, 54, LINE)
    c.text(43, y - 11, "Você", MUTED, 10)
    c.text(43, y + 12, text, FG, 15)


def stages(c, sid, expanded=False):
    busy = sid in {
        "thinking",
        "tool-running",
        "streaming",
        "validating",
        "stages-expanded",
        "plan-collapsed",
        "plan-expanded",
    }
    c.text(
        34,
        195,
        ("▾" if expanded else "▸")
        + " Etapas · "
        + ("3 ações" if busy else "4 ações")
        + " · detalhes",
        MUTED,
        12,
    )
    if busy and not expanded:
        c.text(51, 222, "✓", GOOD, 12)
        c.text(73, 222, "Leu src/auth.py", MUTED, 12)
        c.text(51, 249, "◌", ACCENT, 12)
        c.text(
            73,
            249,
            "Executando os testes de permissões"
            if sid == "validating"
            else "Conferindo as regras de acesso",
            MUTED,
            12,
        )
    if expanded:
        c.rect(40, 215, 1, 270, LINE)
        c.text(58, 236, "ATIVIDADE", FAINT, 10)
        for y, item in zip(
            [265, 293, 321],
            [
                "✓ Leu src/auth.py · 1–80",
                "✓ Buscou can_edit nos testes",
                "◌ Conferindo o comportamento esperado",
            ],
            strict=True,
        ):
            c.text(58, y, item, MUTED, 13)
        c.text(58, 363, "RACIOCÍNIO DO MODELO", FAINT, 10)
        c.text(58, 391, "▾ Conteúdo recebido do modelo", MUTED, 12)
        c.paragraph(
            58,
            420,
            (
                "Exemplo: vou comparar a regra atual com os testes e verificar se "
                "usuários ausentes são tratados antes de acessar suas permissões."
            ),
            99,
            MUTED,
            12,
        )
    return 298 if busy else 249


def assistant(c, y, paragraphs):
    c.text(34, y, "Codaro", ACCENT, 15, True)
    y += 34
    for p in paragraphs:
        y = c.paragraph(34, y, p, 102)
        y += 12
    return y


def chat(c, state):
    sid, kind = state["id"], state["kind"]
    if sid == "welcome":
        return
    if sid in {"welcome", "first-run"}:
        c.text(
            34,
            209,
            "Vamos conectar seu primeiro modelo."
            if sid == "first-run"
            else "O que vamos fazer neste projeto?",
            FG,
            21,
        )
        c.text(
            34,
            246,
            "Use sua própria chave ou um modelo local."
            if sid == "first-run"
            else "Pergunte sobre o código ou descreva uma mudança.",
            MUTED,
            14,
        )
        if sid == "first-run":
            c.button(34, 284, "Conectar provedor", True)
            c.text(34, 345, "A chave é configurada fora da conversa.", FAINT, 12)
        return
    if sid.startswith("slash"):
        items = DATA["commands"] if sid == "slash" else DATA["commands"][:2]
        if sid == "slash-empty":
            c.rect(30, 540, W - 60, 65, PANEL, LINE, 3)
            c.text(48, 576, "Nenhum comando encontrado. Esc fecha a lista.", MUTED, 13)
        else:
            items = items[:5]
            top = 607 - (len(items) * 37 + 12)
            c.rect(30, top, W - 60, len(items) * 37 + 12, PANEL, LINE, 3)
            for i, command in enumerate(items):
                y = top + 6 + i * 37
                if i == (1 if sid == "slash" else 0):
                    c.rect(36, y, W - 72, 35, RAISED, radius=2)
                c.text(
                    48,
                    y + 24,
                    command["name"],
                    ACCENT if i == (1 if sid == "slash" else 0) else FG,
                    13,
                )
                c.text(W - 50, y + 24, command["description"], MUTED, 12, anchor="end")
        return
    if sid == "plan-chat":
        user(c, "Como funciona a autenticação?")
        c.text(34, 208, "3 ações · 2 arquivos consultados", MUTED, 11)
        assistant(
            c,
            241,
            [
                (
                    "O fluxo começa em src/auth.py. Falta tratar usuários ausentes ant"
                    "es de consultar as permissões."
                )
            ],
        )
        user(c, "Corrija esse caso mantendo o contrato atual.", 346)
        c.text(34, 404, "◌ Corrigindo a validação · plano em andamento abaixo", MUTED, 12)
        return
    if sid == "conversation":
        user(c, "Como funciona a autenticação?")
        c.text(34, 208, "3 ações · 2 arquivos consultados", MUTED, 11)
        assistant(
            c,
            249,
            [
                (
                    "O fluxo começa em src/auth.py. A função can_edit confere as permi"
                    "ssões antes de liberar uma edição."
                ),
                "Falta tratar o caso de usuário ausente antes de consultar is_admin.",
            ],
        )
        c.line(30, 377, W - 60)
        user(c, "Corrija esse caso mantendo o contrato atual.", 423)
        c.text(34, 482, "4 ações · implementação e testes", MUTED, 11)
        assistant(
            c, 515, ["Adicionei a verificação de usuário ausente e mantive a assinatura da função."]
        )
        c.text(34, 596, "✓ 24 testes passaram · 2 arquivos alterados", GOOD, 12)
        return
    user(c)
    y = stages(c, sid, sid == "stages-expanded")
    if sid in {"thinking", "tool-running", "stages-expanded", "plan-collapsed", "plan-expanded"}:
        return
    if kind == "question":
        return
    if sid == "streaming":
        assistant(
            c,
            y + 12,
            [
                (
                    "A autenticação começa em src/auth.py. Estou organizando as melhor"
                    "ias com base nos arquivos consultados. ▎"
                )
            ],
        )
    elif sid == "answer":
        assistant(
            c,
            y,
            [
                (
                    "A autenticação começa em src/auth.py e verifica as permissões do "
                    "usuário antes de permitir edições."
                ),
                "Encontrei duas melhorias:",
                "1. Tratar usuários ausentes antes de acessar permissões.",
                "2. Cobrir acesso permitido e negado nos testes.",
            ],
        )
    elif sid == "ask-followup":
        assistant(c, 249, [DATA["questions"]["ask-choice"]["title"]])
        user(c, "Preservar o contrato atual", 357)
        assistant(c, 433, ["Vou respeitar essa decisão ao organizar a implementação e os testes."])
    elif sid == "plan":
        assistant(c, 249, ["Vou preservar o contrato atual da autenticação."])
        for yy, label in zip(
            [334, 372, 410],
            [
                "1  Validar usuários ausentes em auth.py",
                "2  Adicionar testes de permissões",
                "3  Executar testes e conferir o resultado",
            ],
            strict=True,
        ):
            c.text(35, yy, label, MUTED, 14)
        c.button(34, 450, "Executar com revisão", True)
        c.button(231, 450, "Ajustar plano")
    elif sid == "validating":
        assistant(
            c,
            300,
            [
                (
                    "As alterações foram aplicadas. Estou verificando os testes relaci"
                    "onados antes de concluir."
                )
            ],
        )
    elif sid == "done":
        assistant(
            c, 249, ["Corrigi a validação de usuários ausentes e ampliei os testes de autorização."]
        )
        c.text(34, 355, "✓ 24 testes passaram · 2 arquivos alterados", GOOD, 13)
        c.text(34, 385, "Resultado ilustrativo do protótipo.", FAINT, 11)
        c.button(34, 421, "Ver alterações")
    elif sid == "context-compacting":
        assistant(
            c,
            249,
            [
                "Vou reorganizar o contexto para continuar a análise.",
                "O histórico permanece disponível para recuperação.",
            ],
        )
    elif sid in {"connection-error", "auth-error", "cancelled", "resumed", "no-results"}:
        data = {
            "connection-error": (
                "A conexão com o provedor foi interrompida.",
                "Seu progresso e rascunho foram preservados.",
                "Tentar novamente",
                "Trocar modelo",
            ),
            "auth-error": (
                "Não foi possível autenticar no provedor.",
                "Confira a chave cadastrada para continuar.",
                "Configurar provedor",
                "Detalhes",
            ),
            "cancelled": (
                "Execução interrompida.",
                "Alterações já aplicadas permanecem no projeto.",
                "Revisar e retomar",
                "Ver alterações",
            ),
            "resumed": (
                "Conversa anterior retomada.",
                "Vou conferir o estado atual dos arquivos antes de continuar.",
                "Continuar",
                "Ver plano",
            ),
            "no-results": (
                "Não encontrei correspondências para essa busca.",
                "Posso procurar pelo fluxo de login ou você pode indicar um arquivo.",
                "Buscar pelo fluxo de login",
                "Indicar arquivo",
            ),
        }
        title, description, primary, secondary = data[sid]
        c.rect(34, 250, 2, 170, BAD if sid in {"connection-error", "auth-error"} else LINE)
        c.text(52, 278, title, FG, 15)
        c.paragraph(52, 315, description, 105, MUTED, 13)
        x = 52 + c.button(52, 366, primary, True) + 9
        c.button(x, 366, secondary)


def mode_menu(c, state):
    expanded = state["id"] == "edit-approval"
    x, y, width, height = 530, 90, 560, 362 if expanded else 246
    c.rect(x, y, width, height, PANEL, LINE, 4)
    c.text(x + 14, y + 26, "Modo de trabalho", FG, 13)
    for index, mode in enumerate(DATA["modes"]):
        top = y + 42 + index * 36
        if index == 2:
            c.rect(x + 10, top, width - 20, 32, RAISED, LINE, 2)
            c.text(x + 16, top + 22, "›", ACCENT, 15)
        c.text(x + 36, top + 22, mode["name"], FG, 12)
        c.text(x + 126, top + 22, mode["description"], MUTED, 11)
        if index == 2:
            c.text(x + width - 16, top + 22, "Atual", MUTED, 10, anchor="end")
    c.text(
        x + 14,
        y + 172,
        "Edições: " + ("aprovar automaticamente ▴" if expanded else "pedir autorização ▾"),
        MUTED,
        11,
    )
    if expanded:
        c.text(x + 22, y + 198, "○ Pedir autorização", MUTED, 12)
        c.text(x + 22, y + 226, "◉ Aprovar automaticamente", FG, 12)
        c.paragraph(
            x + 22,
            y + 259,
            (
                "Vale para criar e editar arquivos do projeto nesta conversa. Coma"
                "ndos e ações destrutivas continuam com confirmação."
            ),
            73,
            MUTED,
            11,
        )
    c.line(x + 12, y + height - 50, width - 24)
    c.text(x + 14, y + height - 20, "↑↓ navegar · Enter aplicar · Esc fechar", FAINT, 10)
    c.button(x + width - 90, y + height - 40, "Aplicar", True, width=76)


def modal(c, state):
    kind, sid = state["kind"], state["id"]
    if kind in {"chat", "question", "welcome", "first-run"}:
        return
    if kind == "mode":
        mode_menu(c, state)
        return
    c.rect(0, 32, W, H - 32, "#080d14", opacity=0.78)
    x, y, width, height = 220, 123, 680, 502
    c.rect(x, y, width, height, PANEL, LINE, 5)
    c.text(x + width - 22, y + 30, "Esc voltar", MUTED, 11, anchor="end")
    c.line(x, y + height - 60, width)
    fx, fy = x + 26, y + 82
    titles = {
        "provider": "Conectar provedor",
        "models": "Escolher modelo",
        "review": "Revisar alterações · 2 arquivos",
        "command": "Autorizar comando",
        "preferences": "Preferências",
        "mode": "Modo de trabalho",
        "status": "Detalhes da sessão",
        "help": "Comandos e atalhos",
        "new-session": "Começar outra conversa?",
    }
    c.text(fx, y + 32, titles[kind], FG, 16)
    by = y + height - 45
    if kind == "provider":
        ollama, custom = sid == "provider-ollama", sid == "provider-custom"
        fy = c.field(
            fx,
            fy,
            "Provedor",
            "ollama" if ollama else "openai-compatible" if custom else "openai",
            628,
        )
        if not ollama:
            fy = c.field(fx, fy, "API key", "••••••••••••••••", 628)
        if ollama or custom:
            fy = c.field(
                fx,
                fy,
                "URL do provedor",
                "http://localhost:11434" if ollama else "https://meu-servidor/v1",
                628,
            )
        c.text(fx, fy + 8, "▾ Avançado" if custom else "▸ Avançado", MUTED, 12)
        if custom:
            c.text(fx, fy + 39, "☑ TLS Insecure · ignorar verificação de certificado", MUTED, 12)
            c.text(fx, fy + 66, "A conexão não verificará a identidade do servidor.", WARN, 11)
        else:
            c.text(fx, fy + 42, "A credencial fica fora da conversa.", FAINT, 11)
        text, color = {
            "provider-testing": ("◌ Consultando o catálogo…", MUTED),
            "provider-success": ("✓ Conexão verificada · catálogo disponível", GOOD),
            "provider-error": ("Não foi possível conectar. Confira a URL e a credencial.", BAD),
        }.get(sid, ("O teste consulta o catálogo, sem inferência.", MUTED))
        c.text(fx, y + height - 88, text, color, 11)
        c.button(fx, by, "Cancelar teste" if sid == "provider-testing" else "Testar conexão")
        c.button(x + width - 260, by, "Salvar e escolher modelo", True, 234)
    elif kind == "models":
        c.text(fx, fy, "Provedor: Meu provedor", MUTED, 12)
        fy = c.field(fx, fy + 31, "Buscar modelo", "Digite para filtrar…", 628)
        if sid == "models-loading":
            c.text(fx, fy + 55, "◌ Atualizando catálogo…", MUTED, 14)
        elif sid == "models-empty":
            c.text(fx, fy + 55, "Nenhum modelo encontrado.", MUTED, 14)
        else:
            for i, model in enumerate(DATA["models"]):
                top = fy - 4 + i * 39
                selected = i == (3 if sid == "model-fallback" else 0)
                if selected:
                    c.rect(fx - 5, top, 638, 35, RAISED, LINE, 2)
                c.text(fx + 5, top + 24, model["id"], FAINT if model["tools"] is False else FG, 12)
                c.text(
                    x + width - 36,
                    top + 24,
                    "sem conversa" if model["tools"] is False else model["context"],
                    MUTED,
                    11,
                    anchor="end",
                )
        description = (
            "A API não informa a janela; usaremos um orçamento conservador."
            if sid == "model-fallback"
            else "✓ Ciclo de ferramentas e resposta verificado · exemplo fictício"
            if sid == "model-tools"
            else "Contexto e capacidades vêm da API quando disponíveis."
        )
        c.paragraph(
            fx, y + height - 100, description, 80, WARN if sid == "model-fallback" else MUTED, 11
        )
        c.button(fx, by, "Atualizar catálogo")
        c.button(x + width - 155, by, "Usar modelo", True, 129)
    elif kind == "review":
        c.paragraph(
            fx, fy, "Aplicar permitirá que o agente continue para a validação.", 75, MUTED, 12
        )
        c.text(fx, fy + 50, "src/auth.py", MUTED, 12)
        c.rect(fx, fy + 67, 628, 147, BG, LINE, 3)
        for yy, line, color in [
            (fy + 96, "@@ can_edit(user)", MUTED),
            (fy + 122, "− return user.is_admin", BAD),
            (fy + 148, "+ if user is None:", GOOD),
            (fy + 174, "+     return False", GOOD),
            (fy + 200, "+ return user.is_admin", GOOD),
        ]:
            c.text(fx + 14, yy, line, color, 13)
        c.text(
            fx,
            fy + 247,
            "tests/test_auth.py · testes de usuário ausente e acesso negado",
            MUTED,
            11,
        )
        c.button(fx, by, "Rejeitar")
        c.button(x + width - 215, by, "Aplicar e continuar", True, 189)
    elif kind == "command":
        c.paragraph(fx, fy, "Executar os testes relacionados à autenticação.", 78)
        c.rect(fx, fy + 45, 628, 54, BG, LINE, 3)
        c.text(fx + 13, fy + 78, "python -m pytest tests/test_auth.py -q", FG, 13)
        c.text(fx, fy + 139, "Diretório: ~/projetos/codaro", MUTED, 12)
        c.text(fx, fy + 174, "Esta autorização vale somente para o comando exibido.", MUTED, 12)
        c.button(fx, by, "Rejeitar")
        c.button(x + width - 155, by, "Executar", True, 129)
    elif kind == "preferences":
        fy = c.field(fx, fy, "Modo ao iniciar", "Executar ▾", 628)
        c.text(fx, fy + 11, "☑ Oferecer retomada da última conversa", MUTED, 13)
        c.text(fx, fy + 50, "☑ Mostrar resumos curtos das etapas", FG, 13)
        c.paragraph(
            fx, fy + 85, "Raciocínio completo e dados técnicos ficam nos detalhes.", 82, MUTED, 12
        )
        c.button(fx, by, "Cancelar")
        c.button(x + width - 225, by, "Salvar preferências", True, 199)
    elif kind in {"status", "help"}:
        items = (
            [
                ("Projeto", "~/projetos/codaro"),
                ("Janela total", "131.072 tokens"),
                ("Reserva de saída", "8.192 tokens"),
                ("Margem", "512 tokens"),
                ("Orçamento de entrada", "122.368 tokens"),
                ("Entrada estimada", "16.000 tokens"),
                ("Origem", "API: contexto do modelo"),
                ("Diagnóstico", ".codaro/prompt.json"),
            ]
            if kind == "status"
            else [
                ("Abrir comandos", "/ ou Ctrl+P"),
                ("Selecionar comando", "↑↓ e Enter"),
                ("Completar sem executar", "Tab"),
                ("Fechar lista ou modal", "Esc"),
                ("Enviar mensagem", "Enter"),
                ("Nova linha", "Alt+Enter"),
                ("Interromper execução", "Ctrl+X"),
                ("Detalhes da sessão", "/status"),
            ]
        )
        for i, (a, b) in enumerate(items):
            c.text(fx, fy + i * 35, a, MUTED, 12)
            c.text(x + width - 26, fy + i * 35, b, FG, 12, anchor="end")
        c.button(x + width - 120, by, "Fechar", width=94)
    elif kind == "new-session":
        c.paragraph(fx, fy, "A conversa atual continuará disponível para retomada.", 80)
        c.paragraph(fx, fy + 69, "Seu rascunho será preservado ao voltar.", 80, MUTED, 12)
        c.button(fx, by, "Continuar aqui")
        c.button(x + width - 190, by, "Nova conversa", True, 164)


def main():
    for state in DATA["screens"]:
        canvas = Canvas()
        base(canvas, state)
        plan_active = state["id"] in {
            "plan",
            "plan-collapsed",
            "plan-expanded",
            "plan-chat",
            "review-diff",
            "command-approval",
            "validating",
        }
        chat_bottom = 567 if state["id"] == "plan-collapsed" else 416 if plan_active else 610
        canvas.parts.append(
            '<defs><clipPath id="transcript"><rect x="0" y="99" '
            f'width="{W}" height="{chat_bottom - 99}"/></clipPath></defs>'
            '<g clip-path="url(#transcript)">'
        )
        chat(canvas, state)
        canvas.parts.append("</g>")
        composer(canvas, state)
        modal(canvas, state)
        canvas.save(OUT / (state["id"] + ".svg"), state["title"])
    cards = []
    for state in DATA["screens"]:
        sid, title, group = state["id"], state["title"], state["group"]
        cards.append(
            f'<a class="card" href="prototype.html#{sid}">'
            f'<img loading="lazy" src="screens/{sid}.svg" alt="{html.escape(title)}">'
            f"<strong>{html.escape(title)}</strong>"
            f"<small>{html.escape(group)} · abrir interação</small></a>"
        )
    (ROOT / "gallery.html").write_text(
        (
            '<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta nam'
            'e="viewport" content="width=device-width, initial-scale=1"><title'
            ">Codaro · galeria da proposta</title><style>body{background:#0b10"
            "17;color:#dfe6ef;font:15px/1.6 system-ui,sans-serif;max-width:148"
            "0px;margin:35px auto;padding:0 24px}h1{font-size:24px}a{color:#83"
            "d6d7}header{margin-bottom:28px}.grid{display:grid;grid-template-c"
            "olumns:repeat(auto-fit,minmax(350px,1fr));gap:22px}.card{display:"
            "flex;flex-direction:column;text-decoration:none;gap:7px}img{width"
            ":100%;border:1px solid #2a394b;border-radius:5px}small{color:#94a"
            "3b6}strong{font-weight:500}</style><header><h1>Codaro · galeria d"
            "e UI/UX</h1><p>"
        )
        + str(len(DATA["screens"]))
        + (
            " estados da proposta consolidada. Os resumos curtos das etapas ap"
            "arecem durante a execução; detalhes completos ficam recolhidos.</"
            'p><p><a href="prototype.html">Abrir protótipo navegável</a> · <a '
            'href="README.md">Ler as decisões de UX</a></p><small>Maquetes vet'
            "oriais da especificação; dados fictícios. Não são capturas da apl"
            'icação implementada.</small></header><div class="grid">'
        )
        + "".join(cards)
        + "</div></html>"
    )
    selected = [
        "welcome",
        "thinking",
        "stages-expanded",
        "streaming",
        "answer",
        "conversation",
        "plan-chat",
        "slash",
        "ask-choice",
        "ask-text",
        "ask-followup",
        "provider",
        "provider-custom",
        "models",
        "mode",
        "edit-approval",
        "plan-collapsed",
        "plan-expanded",
        "review-diff",
    ]
    with tempfile.TemporaryFile() as log:
        for sid in selected:
            subprocess.run(
                [
                    "inkscape",
                    str(OUT / (sid + ".svg")),
                    "--export-type=png",
                    "--export-filename=" + str(OUT / (sid + ".png")),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    print(f"Rendered {len(DATA['screens'])} SVG mockups and {len(selected)} PNG previews")


if __name__ == "__main__":
    main()
