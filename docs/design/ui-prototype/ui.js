/* View layer for the review artifact. This is not Codaro's production UI. */
(() => {
  const C = globalThis.CodaroPrototype;
  const byId = id => document.getElementById(id);
  const escape = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const params = new URLSearchParams(location.search);
  let state = C.initial(location.hash.slice(1) || params.get("screen") || "welcome");
  let autoTimer;
  let lastRoute = state.route;
  let viewport = params.get("size") || "desktop";
  const scrollCache = new Map();
  if (params.has("capture")) document.body.classList.add("capture");
  const go = (target, label, cls = "", disabled = false) => `<button class="${cls}" data-go="${target}" ${disabled ? "disabled" : ""}>${label}</button>`;
  const action = (name, label, cls = "", extra = "") => `<button class="${cls}" data-action="${name}" ${extra}>${label}</button>`;
  const number = n => Number(n).toLocaleString("pt-BR");
  const compact = n => n >= 1000 ? Math.round(n / 1000) + "k" : number(n);
  function dispatch(event, options = {}) {
    const old = state;
    state = C.reduce(state, event);
    render({ preserveFocus: options.preserveFocus });
    if (old.route !== state.route) {
      history.replaceState(null, "", "#" + state.route);
      lastRoute = state.route;
      schedule();
    }
  }
  function schedule() {
    clearTimeout(autoTimer);
    if (state.route === "provider-testing") autoTimer = setTimeout(() => dispatch({ type: "PROVIDER_OK" }), 1400);
    if (state.route === "models-loading") autoTimer = setTimeout(() => dispatch({ type: "GO", target: "models" }), 1400);
  }
  function user(text = state.question) { return `<div class="user"><span class="user-label">Você</span>${escape(text)}</div>`; }
  function stages() {
    const live = C.busy.has(state.route);
    let content = `<section class="stages">${action("TOGGLE_DETAILS", `${state.expanded ? "▾" : "▸"} Etapas · ${live ? "3 ações" : "4 ações"}${state.expanded ? "" : " · detalhes"}`, "stage-toggle", `aria-expanded="${state.expanded}"`)}`;
    if (live && state.liveSummary && !state.expanded) {
      content += `<div class="live-steps"><div class="stage-entry"><span class="good">✓</span> Leu src/auth.py</div><div class="stage-entry"><span class="accent">◌</span> ${state.route === "validating" ? "Executando os testes de permissões" : state.route === "tool-running" ? "Buscando testes relacionados" : "Conferindo as regras de acesso"}</div></div>`;
    }
    if (state.expanded) {
      content += `<div class="stage-panel"><div class="stage-label">ATIVIDADE</div><div class="stage-entry"><span class="good">✓</span> Leu src/auth.py <span class="faint">· 1–80</span></div><div class="stage-entry"><span class="good">✓</span> Buscou can_edit nos testes</div><div class="stage-entry"><span class="${live ? "accent" : "good"}">${live ? "◌" : "✓"}</span> ${live ? "Conferindo" : "Conferiu"} o comportamento esperado</div><div class="stage-label">RACIOCÍNIO DO MODELO</div>${action("TOGGLE_REASONING", `${state.reasoningExpanded ? "▾" : "▸"} Conteúdo recebido do modelo`, "thought-toggle", `aria-expanded="${state.reasoningExpanded}"`)}${state.reasoningExpanded ? '<div class="reasoning">Exemplo de conteúdo enviado pelo modelo: vou comparar a regra atual com os testes de autorização e verificar se usuários ausentes são tratados antes de acessar suas permissões.</div>' : ""}<div class="caption">Detalhes de ferramenta e raciocínio são registros distintos.</div></div>`;
    }
    return content + "</section>";
  }
  function answer(content, stream = false) { return `<section class="assistant"><div class="speaker">Codaro</div><div ${stream ? 'class="cursor"' : ""}>${content}</div></section>`; }
  function questionDock() {
    const q = { ...C.questions[state.route] };
    if (state.freeQuestion) q.title = state.freeQuestion;
    const custom = !!state.draft.trim();
    return `<section class="question-dock" aria-labelledby="pending-question"><div class="caption">${state.pauseQuestion ? "Pergunta pendente · tarefa pausada" : "? Pergunta"}</div><h3 id="pending-question">${escape(q.title)}</h3><p class="caption">${escape(q.why)}</p>${q.choices.length ? `<div class="choice-list" role="radiogroup" aria-label="${escape(q.title)}">${q.choices.map((x, i) => `<button class="choice ${state.choice === i && !custom ? "selected" : ""}" role="radio" aria-checked="${state.choice === i && !custom}" data-choice="${i}"><span class="radio">${state.choice === i && !custom ? "◉" : "○"}</span><span>${escape(x)}</span>${i === q.recommended ? '<span class="tag">recomendado</span>' : ""}</button>`).join("")}</div>` : ""}</section>`;
  }
  function recovery(title, description, buttons, severity = "") {
    return user() + stages() + `<section class="notice ${severity}"><h3>${title}</h3><p class="caption">${description}</p><div class="actions">${buttons}</div>${action("TOGGLE_ADVANCED", `${state.advanced ? "▾" : "▸"} Detalhes`, "advanced-toggle")}${state.advanced ? '<div class="caption" style="margin-top:10px">Registro ilustrativo: nenhum arquivo foi perdido. Diagnóstico técnico disponível em /status. Credenciais e conteúdo sensível não aparecem na mensagem principal.</div>' : ""}</section>`;
  }
  function mainContent() {
    const r = state.route;
    if (r === "welcome") return "";
    if (r === "first-run") return `<section class="welcome"><h2>Vamos conectar seu primeiro modelo.</h2><p class="muted">Use sua própria chave ou um modelo local.</p><div class="onboarding">${go("provider", "Conectar provedor", "primary")}</div><p class="caption" style="margin-top:17px">A chave é configurada fora da conversa.</p></section>`;
    if (C.questions[r]) return user() + stages();
    if (r.startsWith("slash")) return "";
    if (["thinking", "tool-running", "stages-expanded", "plan-collapsed", "plan-expanded"].includes(r)) return user() + stages();
    if (r === "streaming") return user() + stages() + answer('A autenticação começa em <code>src/auth.py</code>. Estou organizando as melhorias com base nos arquivos consultados.', true);
    if (r === "answer") return user() + stages() + answer('<p>A autenticação começa em <code>src/auth.py</code> e verifica as permissões do usuário antes de permitir edições.</p><p>Encontrei duas melhorias:</p><ol><li>Tratar usuários ausentes antes de acessar permissões.</li><li>Cobrir acesso permitido e negado nos testes.</li></ol>');
    if (r === "ask-followup") return user() + stages() + answer(`<p>${escape(state.lastQuestion || C.questions["ask-choice"].title)}</p>`) + `<div class="submitted-answer"><div class="caption">Você · decisão</div>${escape(state.lastAnswer || "Preservar o contrato atual")}</div>` + answer(`<p>${state.lastAnswer.includes("Pode alterar") ? "Vou considerar mudanças no contrato e destacar os impactos de compatibilidade no plano." : "Vou respeitar essa decisão ao organizar a implementação e os testes."}</p>`);
    if (["conversation", "plan-chat"].includes(r)) return `<article class="chat-turn">${user("Como funciona a autenticação?")}<div class="past-activity">3 ações · 2 arquivos consultados</div>${answer('<p>O fluxo começa em <code>src/auth.py</code>. Antes de liberar uma edição, a função <code>can_edit</code> confere as permissões do usuário.</p><p>Falta tratar o caso de usuário ausente antes de consultar <code>is_admin</code>.</p>')}</article><article class="chat-turn">${user("Corrija esse caso mantendo o contrato atual.")}<div class="past-activity">${r === "plan-chat" ? "Etapa atual · validação de usuários" : "4 ações · implementação e testes"}</div>${answer(r === "plan-chat" ? '<p>Vou corrigir a validação e conferir os testes. O plano continua junto ao input enquanto você acompanha a conversa.</p>' : '<p>Adicionei a verificação de usuário ausente e mantive a assinatura da função.</p><p class="receipt">✓ 24 testes passaram · 2 arquivos alterados</p>')}</article>`;
    if (r === "plan") return user() + answer(`<p>${state.lastAnswer.includes("Pode alterar") ? "Vou destacar os impactos de compatibilidade no plano." : "Vou preservar o contrato atual da autenticação."}</p><div class="plan"><div class="plan-item current"><span class="number">1</span><span>Validar usuários ausentes em auth.py</span></div><div class="plan-item"><span class="number">2</span><span>Adicionar testes de permissões</span></div><div class="plan-item"><span class="number">3</span><span>Executar testes e conferir o resultado</span></div></div><div class="caption" style="margin-top:14px">Planejar não aplica mudanças. A execução seguirá as aprovações configuradas.</div><div class="row" style="margin-top:18px">` + action("START_PLAN", "Executar com revisão", "primary small") + go("ask-text", "Ajustar o plano", "quiet small") + "</div>");
    if (r === "validating") return user() + stages() + answer('<p>As alterações foram aplicadas. Estou verificando os testes relacionados antes de concluir.</p>');
    if (r === "done") return user() + stages() + answer('<p>Corrigi a validação de usuários ausentes e ampliei os testes de autorização.</p><div class="receipt">✓ 24 testes passaram · 2 arquivos alterados</div><p class="caption">Resultado ilustrativo do protótipo.</p><div class="row">' + go("review-diff", "Ver alterações", "quiet small") + go("new-session", "Nova conversa", "quiet small") + "</div>");
    if (r === "context-compacting") return user() + `<div class="stages"><span class="accent">◌</span> Preservando decisões e resultados importantes.</div>` + answer('<p>Vou reorganizar o contexto para continuar a análise.</p><p class="caption">O histórico permanece disponível para recuperação.</p><div class="row">' + go("thinking", "Continuar demonstração", "quiet small") + "</div>");
    if (r === "connection-error") return recovery("A conexão com o provedor foi interrompida.", "Seu progresso e rascunho foram preservados. Podemos tentar novamente.", go("thinking", "Tentar novamente", "primary") + go("models", "Trocar modelo", "quiet"), "error");
    if (r === "auth-error") return recovery("Não foi possível autenticar no provedor.", "Confira a chave cadastrada para continuar.", go("provider", "Configurar provedor", "primary"), "error");
    if (r === "cancelled" && state.planState?.status === "cancelled") return user() + answer(`<p>Plano cancelado · ${C.executionPlan(state).completed}/4 etapas concluídas.</p><p class="caption">Alterações já aplicadas foram preservadas. Você pode enviar um novo pedido pelo campo abaixo.</p>`);
    if (r === "cancelled") return recovery(state.actionRejected ? "Solicitação rejeitada." : "Execução interrompida.", state.actionRejected ? "Esta ação não foi executada. O progresso das etapas anteriores foi preservado." : "Alterações já aplicadas permanecem no projeto. Você pode retomar ou cancelar o plano pelo painel abaixo.", go("resumed", "Revisar e retomar", "primary") + go("review-diff", "Ver alterações", "quiet"));
    if (r === "resumed") return `<div class="caption" style="margin-bottom:20px">Conversa anterior retomada</div>` + user() + stages() + answer('<p>O plano e as decisões anteriores foram recuperados. Vou conferir o estado atual dos arquivos antes de continuar.</p><div class="row">' + go("thinking", "Continuar", "primary small") + go("plan", "Ver plano", "quiet small") + "</div>");
    if (r === "no-results") return user("Encontre a configuração de autenticação.") + stages() + answer('<p>Não encontrei correspondências para essa busca.</p><p>Posso procurar pelo fluxo de login ou você pode indicar um arquivo.</p><div class="row">' + go("thinking", "Buscar pelo fluxo de login", "quiet small") + go("ask-text", "Indicar arquivo", "quiet small") + "</div>");
    return `<section class="welcome"><h2>O projeto está pronto para trabalhar.</h2><p class="muted">Conversa e rascunho ficam preservados durante a configuração.</p></section>`;
  }
  function phase() {
    if (state.pauseQuestion) return "Tarefa pausada";
    if (state.toast) return state.toast;
    if (["plan-collapsed", "plan-expanded"].includes(state.route)) return "Em execução";
    return ({ thinking: "Analisando · 8s", "tool-running": "Consultando arquivos · 8s", "stages-expanded": "Analisando · 8s", streaming: "Respondendo · 12s", validating: "Validando · 18s", "context-compacting": "Reorganizando contexto", "ask-choice": "Aguardando sua resposta", "ask-text": "Aguardando sua resposta", "context-scope": "Aguardando sua escolha", "ask-followup": "Decisão recebida", "connection-error": "Conexão interrompida", "auth-error": "Configuração necessária", cancelled: "Execução interrompida", done: "Concluído", "first-run": "Selecione um provedor" })[state.route] || "Pronto";
  }
  function menuHtml() {
    if (!C.hasMenu(state)) return "";
    const commands = C.matches(state);
    return `<div class="slash-menu" role="listbox" aria-label="Comandos">${commands.length ? commands.map((x, i) => `<button class="command-option ${i === state.menuIndex % commands.length ? "active" : ""}" role="option" aria-selected="${i === state.menuIndex % commands.length}" data-command="${i}"><span class="cmd">${escape(x.name)}</span><span class="description">${escape(x.description)}</span></button>`).join("") : '<div class="slash-empty">Nenhum comando encontrado. Esc fecha a lista.</div>'}</div>`;
  }
  function planPanel() {
    if (state.planState?.status !== "active") return "";
    const plan = C.executionPlan(state);
    const statuses = { done: "Concluída", running: "Em andamento", pending: "Pendente" };
    return `<section class="execution-plan" aria-label="Plano de execução ativo"><div class="plan-heading">${action("TOGGLE_PLAN", `<span>${state.planExpanded ? "▾" : "▸"}</span><strong>Plano</strong><span>${plan.completed}/${plan.steps.length} concluídas</span><span class="plan-current">${escape(plan.current)}</span>`, "plan-summary", `aria-expanded="${state.planExpanded}" ${state.planExpanded ? 'aria-controls="execution-plan-details"' : ""}`)}${action("CANCEL_PLAN", "Cancelar plano", "quiet small cancel-plan", 'title="Encerra o plano e preserva alterações já aplicadas"')}</div>${state.planExpanded ? `<ol id="execution-plan-details" class="execution-plan-list">${plan.steps.map(step => `<li class="${step.status}"><span class="step-status ${step.status === "done" ? "good" : step.status === "running" ? "accent" : "faint"}" aria-label="${statuses[step.status]}">${step.status === "done" ? "✓" : step.status === "running" ? "◌" : "○"}</span><span>${escape(step.title)}</span></li>`).join("")}</ol>` : ""}</section>`;
  }
  function composer() {
    const hasMenu = C.hasMenu(state), question = !!C.questions[state.route];
    const used = !["welcome", "first-run", "slash", "slash-filter", "slash-empty"].includes(state.route);
    const placeholder = question ? "Escreva sua resposta…" : C.busy.has(state.route) ? "Escreva seu próximo pedido enquanto aguarda…" : "Pergunte ou peça uma mudança…";
    const questionHelp = state.draft.trim() ? '<kbd>Enter</kbd> enviar seu texto · <kbd>Esc</kbd> pausar' : C.questions[state.route]?.choices.length ? '<kbd>↑↓</kbd> escolher · <kbd>Enter</kbd> confirmar <span class="optional">· ou digite sua resposta · Esc pausar</span>' : '<kbd>Enter</kbd> enviar resposta · <kbd>Esc</kbd> pausar';
    const capacity = C.modelDetails(state.model);
    const entry = `<div class="entry"><span class="chevron">›</span><textarea id="draft" aria-label="${question ? "Resposta ao modelo" : "Mensagem para o Codaro"}" ${question ? 'aria-describedby="pending-question"' : ""} placeholder="${placeholder}" spellcheck="false">${escape(state.draft)}</textarea></div>`;
    const response = question ? `<div class="response-card" role="group" aria-labelledby="pending-question">${questionDock()}<div class="response-compose">${entry}<div class="response-actions">${action("PAUSE_QUESTION", "Pausar", "quiet small")}${action("ANSWER", "Enviar ↵", "primary small", !state.draft.trim() && !C.questions[state.route].choices.length ? "disabled" : "")}</div></div></div>` : entry;
    return `<section class="composer ${question ? "answering" : ""}${state.planState?.status === "active" ? " has-plan" : ""}">${menuHtml()}${planPanel()}${response}${state.hint ? `<div class="hint" role="status">${escape(state.hint)}</div>` : ""}<div class="meta" aria-live="polite"><span>${escape(phase())}${state.mode === "Executar" && state.editApproval === "auto" ? ' <span class="auto-edit-status">· Edições automáticas</span>' : ""}</span><span class="capacity" title="Janela: ${number(capacity.total)} · reserva: ${number(capacity.reserve)} · margem: ${number(capacity.margin)} · entrada: ${number(capacity.budget)}. ${capacity.source}. Valores ilustrativos.">${used ? `Contexto ≈${compact(capacity.used)}/${compact(capacity.budget)} · ${capacity.percent}%` : ""}</span></div><div class="shortcuts"><span>${hasMenu ? '<kbd>↑↓</kbd> navegar · <kbd>Enter</kbd> selecionar · <kbd>Esc</kbd> fechar' : question ? questionHelp : C.busy.has(state.route) ? '<kbd>Ctrl+X</kbd> interromper <span class="optional">· rascunho preservado</span>' : '<kbd>Enter</kbd> enviar · <kbd>/</kbd> comandos <span class="optional">· Alt+Enter nova linha</span>'}</span><button data-action="PALETTE">Ctrl+P menu</button></div></section>`;
  }
  function dialog(title, body, actions, wide = false) {
    return `<div class="modal-backdrop"><section class="dialog ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div class="dialog-header"><h2 id="dialog-title">${title}</h2>${action("BACK", "Esc voltar", "close")}</div><div class="dialog-body">${body}</div><div class="dialog-actions">${actions}</div></section></div>`;
  }
  function providerDialog() {
    const kind = state.providerKind;
    const custom = ["openai-compatible", "ollama"].includes(kind);
    const testing = state.route === "provider-testing", success = state.route === "provider-success", failure = state.route === "provider-error";
    const options = ["openai", "anthropic", "gemini", "groq", "ollama", "openai-compatible"];
    const url = ({ ollama: "http://localhost:11434", "openai-compatible": "https://meu-servidor/v1", openai: "https://api.openai.com/v1", anthropic: "https://api.anthropic.com/v1", gemini: "https://generativelanguage.googleapis.com/v1beta", groq: "https://api.groq.com/openai/v1" })[kind];
    let body = `<p class="caption">Conecte um provedor com sua chave ou use um modelo local.</p><div class="field"><label for="provider-kind">Provedor</label><select id="provider-kind" ${testing ? "disabled" : ""}>${options.map(x => `<option ${x === kind ? "selected" : ""}>${x}</option>`).join("")}</select></div>`;
    if (kind !== "ollama") body += `<div class="field"><label for="demo-key">API key</label><input id="demo-key" type="password" value="${escape(state.providerKey)}" placeholder="Use apenas uma chave fictícia neste protótipo" autocomplete="off" ${testing ? "disabled" : ""}><span class="caption">A credencial fica fora da conversa.</span></div>`;
    if (custom) body += `<div class="field"><label for="provider-url">URL do provedor</label><input id="provider-url" value="${escape(state.providerUrl || url)}" spellcheck="false" ${testing ? "disabled" : ""}></div>`;
    body += action("TOGGLE_ADVANCED", `${state.advanced ? "▾" : "▸"} Avançado`, "advanced-toggle");
    if (state.advanced) body += `<div class="advanced"><div class="field"><label for="provider-name">Nome do perfil</label><input id="provider-name" value="${escape(state.providerName)}" ${testing ? "disabled" : ""}></div>${!custom ? `<div class="field"><label for="provider-url">URL da API</label><input id="provider-url" value="${escape(state.providerUrl || url)}" ${testing ? "disabled" : ""}></div>` : ""}<label class="check"><input id="tls-option" type="checkbox" ${state.tls ? "checked" : ""} ${testing ? "disabled" : ""}> TLS Insecure · ignorar verificação de certificado</label>${state.tls ? '<p class="caption warn">A conexão não verificará a identidade do servidor.</p>' : ""}</div>`;
    body += `<div class="inline-status ${success ? "success" : failure ? "failure" : ""}" role="status">${testing ? "◌ Consultando o catálogo…" : success ? "✓ Conexão verificada · catálogo disponível" : failure ? "Não foi possível conectar. Confira a URL e a credencial." : "O teste de conexão consulta o catálogo, sem inferência."}</div>`;
    return dialog("Conectar provedor", body, action(testing ? "PROVIDER_CANCEL_TEST" : "PROVIDER_TEST", testing ? "Cancelar teste" : "Testar conexão", "quiet") + go("models", "Salvar e escolher modelo", "primary", testing || failure));
  }
  function modelsDialog() {
    const loading = state.route === "models-loading", empty = state.route === "models-empty";
    const filtered = C.filteredModels(state);
    const selected = filtered[state.modelIndex];
    let body = `<div class="row spread caption"><span>Provedor: ${escape(state.providerName)}</span>${go("provider", "Trocar provedor", "quiet small")}</div><input id="model-query" class="model-search" placeholder="Buscar modelo…" value="${escape(state.modelQuery)}" aria-label="Buscar modelo"><div class="model-list" role="listbox" aria-label="Modelos">${loading ? '<div class="muted" style="padding:25px 4px">◌ Atualizando catálogo…</div>' : empty || !filtered.length ? '<div class="muted" style="padding:25px 4px">Nenhum modelo encontrado.</div>' : filtered.map((x, i) => `<button class="model-row ${i === state.modelIndex ? "active" : ""} ${x.tools === false ? "incompatible" : ""}" data-model="${i}" role="option" aria-selected="${i === state.modelIndex}"><span>${escape(x.id)}</span><span class="ctx">${x.tools === false ? "sem conversa" : x.context}</span></button>`).join("")}</div><div class="model-description">${loading ? "A seleção atual será preservada." : empty ? "Atualize o catálogo ou confira o provedor." : escape(selected?.detail || "Refine a busca.")}</div>`;
    if (state.route === "model-fallback") body += '<p class="caption warn">A API não informa a janela. O orçamento será conservador até obtermos um limite confiável.</p>';
    if (state.route === "model-tools") body += '<div class="notice"><h3>Teste completo do modelo</h3><p class="caption">Faz duas chamadas curtas para verificar ferramentas e resposta. Pode consumir créditos.</p><div class="receipt">✓ Chamada, resultado da ferramenta e resposta final verificados</div><p class="caption">Resultado fictício para avaliar a apresentação.</p></div>';
    body += action("TOGGLE_ADVANCED", `${state.advanced ? "▾" : "▸"} Detalhes do modelo`, "advanced-toggle");
    if (state.advanced && selected) {
      const info = C.modelDetails(selected.id);
      body += `<div class="advanced"><div class="kv"><span>Janela total</span><span>${info.reported ? number(info.total) + " tokens" : "Não informada"}</span></div><div class="kv"><span>Origem</span><span>${escape(info.source)}</span></div><div class="kv"><span>Ferramentas</span><span>${selected.tools == null ? "Não informado" : selected.tools ? "Informado pela API" : "Sem suporte"}</span></div>` + go("model-tools", "Testar ferramentas e resposta", "quiet small", selected.tools === false) + '</div>';
    }
    return dialog("Escolher modelo", body, go("models-loading", "Atualizar catálogo", "quiet") + action("MODEL_USE", "Usar modelo", "primary", loading || empty || selected?.tools === false || !selected ? "disabled" : ""));
  }
  function modePopover() {
    return `<div class="mode-popover-layer"><section id="mode-popover" class="mode-popover" role="dialog" aria-modal="true" aria-labelledby="mode-title"><h2 id="mode-title">Modo de trabalho</h2><div class="mode-list" role="radiogroup" aria-label="Modo de trabalho">${C.modes.map((mode, index) => `<button class="mode-option ${state.modeIndex === index ? "selected" : ""}" data-mode="${index}" role="radio" aria-checked="${state.modeIndex === index}" title="${escape(mode.scope)}"><span class="mode-caret" aria-hidden="true">${state.modeIndex === index ? "›" : ""}</span><span class="mode-name">${mode.name}</span><span class="mode-description">${mode.description}</span><span class="mode-current">${state.mode === mode.name ? "Atual" : ""}</span></button>`).join("")}</div><div class="mode-permissions">${state.modeIndex === 2 ? action("EDIT_POLICY_TOGGLE", `Edições: ${state.editPolicyDraft === "auto" ? "aprovar automaticamente" : "pedir autorização"} ${state.editPolicyExpanded ? "▴" : "▾"}`, "edit-policy-toggle", `aria-expanded="${state.editPolicyExpanded}"`) : ""}${state.modeIndex === 2 && state.editPolicyExpanded ? `<fieldset class="edit-policy-options"><legend class="visually-hidden">Aprovação de edições</legend>${[["ask", "Pedir autorização"], ["auto", "Aprovar automaticamente"]].map(([value, label]) => `<label><input id="edit-policy-${value}" name="edit-policy" type="radio" value="${value}" ${state.editPolicyDraft === value ? "checked" : ""}>${label}</label>`).join("")}<p>Vale para criar e editar arquivos do projeto nesta conversa. Comandos e ações destrutivas continuam com confirmação.</p></fieldset>` : ""}</div><div class="mode-footer"><span>↑↓ navegar · Enter aplicar · Esc fechar</span>${action("MODE_USE", "Aplicar", "primary small")}</div></section></div>`;
  }
  function modal() {
    const kind = C.screen(state.route).kind;
    if (kind === "provider") return providerDialog();
    if (kind === "models") return modelsDialog();
    if (kind === "review") return dialog("Revisar alterações · 2 arquivos", '<p class="caption">Aplicar permitirá que o agente continue para a validação.</p><div class="row spread caption" style="margin:15px 0 8px"><span>src/auth.py</span><span class="good">+3 <span class="bad">−1</span></span></div><div class="diff"><span class="muted">@@ can_edit(user)</span>\n<span class="remove">− return user.is_admin</span>\n<span class="add">+ if user is None:\n+     return False\n+ return user.is_admin</span></div><div class="caption" style="margin-top:15px">tests/test_auth.py · cenários de usuário ausente e acesso negado</div><p class="caption" style="margin-top:13px">Revise o conjunto antes de autorizar. Nenhuma mudança é aplicada automaticamente.</p>', action("REJECT", "Rejeitar", "quiet danger") + action("BACK", "Voltar", "quiet") + action("APPROVE", "Aplicar e continuar", "primary"), true);
    if (kind === "command") return dialog("Autorizar comando", '<p>Executar os testes relacionados à autenticação.</p><div class="permission-command">python -m pytest tests/test_auth.py -q</div><div class="caption">Diretório: ~/projetos/codaro<br>Limite: 60 segundos</div><p class="caption" style="margin-top:15px">Esta autorização vale somente para o comando exibido.</p>', action("REJECT", "Rejeitar", "quiet") + action("APPROVE", "Executar", "primary"));
    if (kind === "mode") return modePopover();
    if (kind === "preferences") return dialog("Preferências", `<div class="field"><label for="default-mode">Modo ao iniciar uma conversa</label><select id="default-mode">${C.modes.map(mode => `<option ${state.defaultMode === mode.name ? "selected" : ""}>${mode.name}</option>`).join("")}</select></div><label class="check"><input id="resume-option" type="checkbox" ${state.resumeSession ? "checked" : ""}> Oferecer retomada da última conversa</label><label class="check"><input id="summary-option" type="checkbox" ${state.liveSummary ? "checked" : ""}> Mostrar resumos curtos das etapas</label><p class="caption">Raciocínio completo e dados técnicos ficam nos detalhes.</p>`, action("BACK", "Cancelar", "quiet") + action("PREFERENCES_SAVE", "Salvar preferências", "primary"));
    if (kind === "status") {
      const info = C.modelDetails(state.model);
      const fields = [["Projeto", "~/projetos/codaro"], ["Modelo", state.providerName + " / " + state.model], ["Modo", state.mode], ["Janela total", info.reported ? number(info.total) + " tokens" : "Não informada pela API"], ["Reserva de saída", number(info.reserve) + " tokens"], ["Margem", number(info.margin) + " tokens"], ["Orçamento de entrada", number(info.budget) + " tokens"], ["Entrada estimada", number(info.used) + " tokens"], ["Origem do limite", info.source], ["Edições", state.editApproval === "auto" ? "Automáticas nesta conversa" : "Pedir autorização"], ["Comandos", "Pedir autorização"], ["Diagnóstico local", ".codaro/prompt.json"]];
      return dialog("Detalhes da sessão", fields.map(([key, value]) => `<div class="kv"><span>${escape(key)}</span><span>${escape(value)}</span></div>`).join("") + '<p class="caption" style="margin-top:13px">Dados ilustrativos do protótipo.</p>', action("BACK", "Fechar", "quiet"));
    }
    if (kind === "help") return dialog("Comandos e atalhos", '<p class="caption">A ajuda do rodapé muda conforme a interação.</p>' + [ ["Abrir comandos", "/ ou Ctrl+P"], ["Selecionar comando", "↑↓ e Enter"], ["Completar sem executar", "Tab"], ["Fechar lista ou modal", "Esc"], ["Enviar mensagem", "Enter"], ["Nova linha", "Alt+Enter"], ["Interromper execução", "Ctrl+X"], ["Detalhes da sessão", "/status"] ].map(([a, b]) => `<div class="help-row"><span>${a}</span><code>${b}</code></div>`).join("") + '<p class="caption" style="margin-top:14px">No macOS, exibir ⌘ quando recebido pelo terminal e manter Ctrl como alternativa. A validação nativa faz parte da próxima etapa.</p>', action("BACK", "Fechar", "quiet"));
    if (kind === "new-session") return dialog("Começar outra conversa?", `<p>A conversa atual continuará disponível para retomada.</p><p class="caption">Seu rascunho será preservado ao voltar.</p>${state.planState?.status === "active" ? '<p class="caption">Começar outra conversa encerra o plano atual. Arquivos já alterados são mantidos.</p>' : ""}`, action("BACK", "Continuar aqui", "quiet") + action("NEW_SESSION", "Nova conversa", "primary"));
    return "";
  }
  function workspaceHtml() {
    const selection = state;
    // The prototype keeps the originating conversation visible while selecting a mode.
    // Rendering is synchronous; restore the interaction state before handling any event.
    const modeOpen = C.screen(selection.route).kind === "mode";
    if (modeOpen) state = selection.dialogOrigin || { ...selection, route: "welcome", draft: selection.lastDraft };
    try {
      return `<header class="header"><div class="brand"><span class="mark">◈</span>Codaro<span class="repo">codaro</span></div><div class="identity"><button class="quiet model" data-go="${state.route === "first-run" ? "provider" : "models"}" title="Selecionar provedor e modelo">${state.route === "first-run" ? "Conectar modelo" : escape(state.model)}</button><span class="faint">·</span><button id="mode-trigger" class="mode-trigger" data-go="mode" aria-haspopup="dialog" aria-expanded="${modeOpen}" ${modeOpen ? 'aria-controls="mode-popover"' : ""} title="${escape(C.modes.find(x => x.name === state.mode)?.scope)}">${escape(state.mode)} ▾</button>${state.tls ? '<span class="transport-warning" title="A identidade do servidor não será verificada">TLS sem verificação</span>' : ""}</div></header><main class="chat" id="chat">${mainContent()}</main>${composer()}`;
    } finally {
      state = selection;
    }
  }
  function render(options = {}) {
    const sameScene = lastRoute === state.route;
    const chatScroll = byId("chat")?.scrollTop || 0;
    const scrollAreas = [".dialog-body", ".question-dock", ".model-list", ".execution-plan-list"].map(selector => [selector, byId("terminal").querySelector(selector)?.scrollTop || 0]);
    scrollCache.set(lastRoute + ":chat", chatScroll);
    for (const [selector, top] of scrollAreas) scrollCache.set(C.screen(lastRoute).kind + selector, top);
    const activeAction = document.activeElement?.dataset?.action;
    const activeChoice = document.activeElement?.dataset?.choice;
    const activeMode = document.activeElement?.dataset?.mode;
    const previousFocus = document.activeElement?.id;
    const start = document.activeElement?.selectionStart;
    const end = document.activeElement?.selectionEnd;
    const terminal = byId("terminal");
    terminal.className = "terminal " + (viewport === "desktop" ? "" : viewport);
    const modalContent = modal();
    terminal.innerHTML = `<div class="titlebar"><span class="dots"><i></i><i></i><i></i></span><em>Codaro · proposta de UI</em><span>protótipo</span></div><div class="workspace" ${modalContent ? "inert" : ""}>${workspaceHtml()}</div>${modalContent}`;
    byId("screen-select").value = state.route;
    byId("screen-title").textContent = C.screen(state.route).title;
    byId("screen-count").textContent = `${C.screens.findIndex(x => x.id === state.route) + 1} / ${C.screens.length}`;
    if (options.preserveFocus && previousFocus && byId(previousFocus)) {
      const element = byId(previousFocus); element.focus({ preventScroll: true });
      if (typeof start === "number" && typeof element.setSelectionRange === "function") element.setSelectionRange(start, end);
    } else if (sameScene && activeAction && terminal.querySelector(`[data-action="${activeAction}"]`)) terminal.querySelector(`[data-action="${activeAction}"]`).focus({ preventScroll: true });
    else if (sameScene && activeChoice != null && terminal.querySelector(`[data-choice="${state.choice}"]`)) terminal.querySelector(`[data-choice="${state.choice}"]`).focus({ preventScroll: true });
    else if (sameScene && activeMode != null && terminal.querySelector('.mode-option.selected')) terminal.querySelector('.mode-option.selected').focus({ preventScroll: true });
    else if (!modalContent) byId("draft").focus({ preventScroll: true });
    else (terminal.querySelector('.mode-popover .mode-option.selected') || terminal.querySelector('.dialog input:not(:disabled), .dialog select:not(:disabled)') || terminal.querySelector('.dialog button:not(:disabled)'))?.focus({ preventScroll: true });
    const active = terminal.querySelector(".command-option.active");
    if (active) active.parentElement.scrollTop = Math.max(0, active.offsetTop - active.parentElement.clientHeight / 2);
    const draft = byId("draft");
    if (draft) draft.style.height = Math.min(140, Math.max(viewport === "compact" ? 34 : 52, draft.scrollHeight || 0)) + "px";
    const viewRoute = C.screen(state.route).kind === "mode" ? state.dialogOrigin?.route || "welcome" : state.route;
    byId("chat").scrollTop = scrollCache.get(viewRoute + ":chat") || 0;
    const popover = terminal.querySelector('.mode-popover'), anchor = terminal.querySelector('.mode-trigger');
    if (popover && anchor && typeof anchor.getBoundingClientRect === "function") {
      const frame = terminal.getBoundingClientRect(), trigger = anchor.getBoundingClientRect();
      popover.style.left = Math.max(12, Math.min(trigger.right - frame.left - popover.offsetWidth, frame.width - popover.offsetWidth - 12)) + "px";
      popover.style.top = Math.max(36, Math.min(trigger.bottom - frame.top + 8, frame.height - popover.offsetHeight - 12)) + "px";
      popover.style.right = "auto";
    }
    for (const [selector, previousScroll] of scrollAreas) {
      const area = terminal.querySelector(selector);
      if (!area) continue;
      area.scrollTop = scrollCache.get(C.screen(state.route).kind + selector) || 0;
      const selected = area.querySelector('.choice.selected, .model-row.active');
      if (!selected || typeof selected.getBoundingClientRect !== "function") continue;
      const row = selected.getBoundingClientRect(), panel = area.getBoundingClientRect();
      if (row.bottom > panel.bottom) area.scrollTop += row.bottom - panel.bottom;
      else if (row.top < panel.top) area.scrollTop -= panel.top - row.top;
    }
  }
  byId("terminal").addEventListener("click", event => {
    if (C.screen(state.route).kind === "mode" && event.target.matches?.('.mode-popover-layer')) return dispatch({ type: "BACK" });
    const button = event.target.closest("button"); if (!button || button.disabled) return;
    if (button.dataset.go) return dispatch({ type: "GO", target: button.dataset.go });
    if (button.dataset.command != null) { state = { ...state, menuIndex: Number(button.dataset.command) }; return dispatch({ type: "KEY", key: "Enter" }); }
    if (button.dataset.choice != null) return dispatch({ type: "CHOICE", index: Number(button.dataset.choice) });
    if (button.dataset.model != null) return dispatch({ type: "MODEL_SELECT", index: Number(button.dataset.model) });
    if (button.dataset.mode != null) return dispatch({ type: "MODE_SELECT", index: Number(button.dataset.mode) });
    if (button.dataset.action) dispatch({ type: button.dataset.action });
  });
  byId("terminal").addEventListener("input", event => {
    if (event.target.id === "draft") dispatch({ type: "DRAFT", value: event.target.value }, { preserveFocus: true });
    if (event.target.id === "model-query") dispatch({ type: "MODEL_QUERY", value: event.target.value }, { preserveFocus: true });
    const field = { "demo-key": "providerKey", "provider-url": "providerUrl", "provider-name": "providerName" }[event.target.id];
    if (field) dispatch({ type: "PROVIDER_FIELD", name: field, value: event.target.value }, { preserveFocus: true });
  });
  byId("terminal").addEventListener("change", event => {
    if (event.target.name === "edit-policy") dispatch({ type: "EDIT_POLICY_SELECT", value: event.target.value }, { preserveFocus: true });
    if (event.target.id === "provider-kind") dispatch({ type: "PROVIDER_KIND", value: event.target.value });
    if (event.target.id === "tls-option") dispatch({ type: "TLS", value: event.target.checked });
    if (event.target.id === "default-mode") dispatch({ type: "PREFERENCE", name: "defaultMode", value: event.target.value }, { preserveFocus: true });
    if (event.target.id === "resume-option") dispatch({ type: "PREFERENCE", name: "resumeSession", value: event.target.checked }, { preserveFocus: true });
    if (event.target.id === "summary-option") dispatch({ type: "PREFERENCE", name: "liveSummary", value: event.target.checked }, { preserveFocus: true });
  });
  document.addEventListener("keydown", event => {
    if (event.isComposing) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p") { event.preventDefault(); if (!modal()) dispatch({ type: "PALETTE" }); return; }
    if (event.key === "Tab" && modal()) {
      const controls = [...byId("terminal").querySelectorAll('.dialog button:not(:disabled), .dialog input:not(:disabled), .dialog select:not(:disabled), .mode-popover button:not(:disabled), .mode-popover input:not(:disabled)')];
      const index = controls.indexOf(document.activeElement);
      if (controls.length && (index < 0 || !event.shiftKey && index === controls.length - 1 || event.shiftKey && index === 0)) { event.preventDefault(); controls[event.shiftKey ? controls.length - 1 : 0].focus(); }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "x" && C.busy.has(state.route)) { event.preventDefault(); dispatch({ type: "CANCEL" }); return; }
    if (event.key === "Escape") { event.preventDefault(); dispatch({ type: "KEY", key: "Escape" }); return; }
    const target = event.target;
    if (C.screen(state.route).kind === "mode" && target.closest?.(".mode-list") && ["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) {
      event.preventDefault();
      if (event.key === "Enter") dispatch({ type: "MODE_USE" });
      else dispatch({ type: "MODE_SELECT", index: (state.modeIndex + (event.key === "ArrowDown" ? 1 : -1) + C.modes.length) % C.modes.length });
      return;
    }
    if (C.screen(state.route).kind === "models" && (target.id === "model-query" || target.closest?.(".model-list")) && ["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) {
      event.preventDefault();
      const count = C.filteredModels(state).length;
      if (event.key === "Enter") dispatch({ type: "MODEL_USE" });
      else dispatch({ type: "MODEL_SELECT", index: count ? (state.modelIndex + (event.key === "ArrowDown" ? 1 : -1) + count) % count : 0 });
      return;
    }
    if (target.id !== "draft") {
      if (C.questions[state.route] && target.closest?.(".choice-list") && ["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) { event.preventDefault(); if (event.key === "Enter" && target.dataset.choice != null) state = C.reduce(state, { type: "CHOICE", index: Number(target.dataset.choice) }); dispatch({ type: "KEY", key: event.key }); }
      return;
    }
    if (event.key === "Enter" && (event.altKey || event.shiftKey)) { event.preventDefault(); target.setRangeText("\n", target.selectionStart, target.selectionEnd, "end"); dispatch({ type: "DRAFT", value: target.value }, { preserveFocus: true }); return; }
    if (["Enter", "ArrowDown", "ArrowUp", "Tab"].includes(event.key) && !(event.key === "Tab" && !C.matches(state).length) && (event.key === "Enter" || C.hasMenu(state) || C.questions[state.route] && !state.draft)) {
      event.preventDefault(); dispatch({ type: "KEY", key: event.key });
    }
  });
  const select = byId("screen-select");
  for (const group of [...new Set(C.screens.map(x => x.group))]) {
    const optgroup = document.createElement("optgroup"); optgroup.label = group;
    C.screens.filter(x => x.group === group).forEach(x => { const option = document.createElement("option"); option.value = x.id; option.textContent = x.title; optgroup.appendChild(option); });
    select.appendChild(optgroup);
  }
  select.addEventListener("change", event => dispatch({ type: "SCREEN", id: event.target.value }));
  byId("previous").onclick = () => dispatch({ type: "SCREEN", id: C.screens[(C.screens.findIndex(x => x.id === state.route) - 1 + C.screens.length) % C.screens.length].id });
  byId("next").onclick = () => dispatch({ type: "SCREEN", id: C.screens[(C.screens.findIndex(x => x.id === state.route) + 1) % C.screens.length].id });
  byId("viewport").onchange = event => { viewport = event.target.value; render(); };
  byId("viewport").value = viewport;
  byId("catalog-toggle").onclick = () => byId("catalog").classList.toggle("open");
  byId("catalog").innerHTML = [...new Set(C.screens.map(x => x.group))].map(group => `<h2>${escape(group)}</h2><div class="items">${C.screens.filter(x => x.group === group).map(x => `<button data-screen="${x.id}">${escape(x.title)}<small>${x.id}</small></button>`).join("")}</div>`).join("");
  byId("catalog").onclick = event => { const button = event.target.closest("button[data-screen]"); if (button) { dispatch({ type: "SCREEN", id: button.dataset.screen }); byId("terminal").scrollIntoView({ behavior: "smooth", block: "start" }); } };
  window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id !== state.route) dispatch({ type: "SCREEN", id }); });
  render(); schedule();
  globalThis.CodaroPrototypeUI = { getState: () => ({ ...state }), dispatch };
})();
