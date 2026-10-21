/* Standalone interaction model. Fictional data; no API or filesystem access. */
(() => {
  const screens = [
    ["welcome", "Início com modelo configurado", "Início", "welcome"],
    ["first-run", "Primeiro acesso", "Início", "first-run"],
    ["slash", "Menu / com navegação", "Entrada", "chat"],
    ["slash-filter", "Comandos filtrados", "Entrada", "chat"],
    ["slash-empty", "Comando não encontrado", "Entrada", "chat"],
    ["thinking", "Modelo analisando", "Conversa", "chat"],
    ["tool-running", "Ferramenta em execução", "Conversa", "chat"],
    ["stages-expanded", "Atividade e raciocínio expandidos", "Conversa", "chat"],
    ["streaming", "Resposta em streaming", "Conversa", "chat"],
    ["answer", "Resposta final", "Conversa", "chat"],
    ["conversation", "Conversa com várias mensagens", "Conversa", "chat"],
    ["ask-choice", "Pergunta com opções", "Perguntas", "question"],
    ["ask-text", "Pergunta com resposta livre", "Perguntas", "question"],
    ["ask-followup", "Resposta do usuário e continuação", "Perguntas", "chat"],
    ["plan", "Plano para revisar", "Execução", "chat"],
    ["plan-collapsed", "Plano de execução recolhido", "Execução", "chat"],
    ["plan-expanded", "Plano de execução expandido", "Execução", "chat"],
    ["plan-chat", "Plano fixo durante a conversa", "Execução", "chat"],
    ["review-diff", "Aprovação de alterações", "Execução", "review"],
    ["command-approval", "Aprovação de comando", "Execução", "command"],
    ["validating", "Validação em andamento", "Execução", "chat"],
    ["done", "Tarefa concluída", "Execução", "chat"],
    ["provider", "Cadastro de provedor", "Provedores", "provider"],
    ["provider-custom", "OpenAI compatible e TLS", "Provedores", "provider"],
    ["provider-ollama", "Ollama local", "Provedores", "provider"],
    ["provider-testing", "Teste de conexão em andamento", "Provedores", "provider"],
    ["provider-success", "Conexão verificada", "Provedores", "provider"],
    ["provider-error", "Erro ao testar conexão", "Provedores", "provider"],
    ["models", "Busca e seleção de modelo", "Modelos", "models"],
    ["models-loading", "Atualização do catálogo", "Modelos", "models"],
    ["models-empty", "Catálogo sem resultados", "Modelos", "models"],
    ["model-fallback", "Contexto não informado pela API", "Modelos", "models"],
    ["model-tools", "Diagnóstico de ferramentas", "Modelos", "models"],
    ["preferences", "Preferências", "Configuração", "preferences"],
    ["mode", "Escolha do modo", "Configuração", "mode"],
    ["edit-approval", "Aprovação de edições", "Configuração", "mode"],
    ["status", "Detalhes da sessão e contexto", "Configuração", "status"],
    ["help", "Ajuda e atalhos", "Configuração", "help"],
    ["context-compacting", "Reorganização automática do contexto", "Recuperação", "chat"],
    ["connection-error", "Conexão interrompida", "Recuperação", "chat"],
    ["auth-error", "Credencial inválida", "Recuperação", "chat"],
    ["cancelled", "Cancelamento com progresso preservado", "Recuperação", "chat"],
    ["resumed", "Retomada da sessão", "Recuperação", "chat"],
    ["no-results", "Busca sem resultados", "Recuperação", "chat"],
    ["context-scope", "Escolha de escopo para continuar", "Recuperação", "question"],
    ["new-session", "Nova conversa sem perder rascunho", "Configuração", "new-session"],
  ].map(([id, title, group, kind]) => ({ id, title, group, kind }));

  const commands = [
    ["/models", "Selecionar modelo", "models"],
    ["/mode", "Alterar modo", "mode"],
    ["/providers", "Gerenciar provedores", "provider"],
    ["/plan", "Revisar o plano", "plan"],
    ["/new", "Nova conversa", "new-session"],
    ["/resume", "Retomar conversa", "resumed"],
    ["/compact", "Reorganizar contexto", "context-compacting"],
    ["/settings", "Preferências", "preferences"],
    ["/status", "Detalhes da sessão", "status"],
    ["/help", "Ajuda e atalhos", "help"],
  ].map(([name, description, target]) => ({ name, description, target }));

  const models = [
    { id: "qwen3.8:27b", context: "128k", tokens: 131072, tools: true, detail: "Contexto informado pela API" },
    { id: "qwen2.5-coder:7b", context: "32k", tokens: 32768, tools: true, detail: "Contexto informado pela API" },
    { id: "llama3.1:8b", context: "128k", tokens: 131072, tools: true, detail: "Contexto informado pela API" },
    { id: "modelo-sem-metadados", context: "Não informado", tools: null, detail: "Usaremos um orçamento conservador; ajuste em Avançado." },
    { id: "embedding-local", context: "—", tools: false, detail: "Modelo de embeddings; indisponível para conversa." },
  ];

  const modes = [
    { name: "Perguntar", description: "Entender e tirar dúvidas", scope: "Leituras e buscas, sem alterar arquivos ou executar comandos." },
    { name: "Planejar", description: "Investigar e preparar um plano", scope: "Entrega um plano para revisar antes de iniciar a implementação." },
    { name: "Executar", description: "Implementar e validar", scope: "Edições e comandos seguem as permissões da sessão." },
  ];
  const busy = new Set(["thinking", "tool-running", "stages-expanded", "streaming", "validating", "context-compacting", "plan-collapsed", "plan-expanded"]);
  const dialogKinds = new Set(["provider", "models", "mode", "preferences", "status", "help", "new-session", "review", "command"]);
  const questions = {
    "ask-choice": { title: "Podemos alterar o contrato da autenticação?", why: "Essa escolha define se a mudança pode exigir ajustes nos clientes.", choices: ["Preservar o contrato atual", "Pode alterar o contrato", "Preciso de outra abordagem"], recommended: 0 },
    "ask-text": { title: "Há alguma restrição que eu deva respeitar?", why: "Descreva o contrato ou comportamento que deve ser preservado.", choices: [], recommended: null },
    "context-scope": { title: "Por onde devemos continuar a análise?", why: "Vou investigar uma parte por vez para preservar os detalhes relevantes.", choices: ["Autenticação e permissões", "Configuração dos provedores", "Escolher outros arquivos"], recommended: 0 },
  };

  function screen(id) { return screens.find(x => x.id === id) || screens[0]; }
  function initial(id = "welcome", previous = {}) {
    const selected = screen(id);
    return {
      route: selected.id, draft: ({ slash: "/", "slash-filter": "/mo", "slash-empty": "/xyz" })[id] || "",
      question: "Analise a autenticação e proponha melhorias.", model: previous.model || "qwen3.8:27b",
      mode: previous.mode || "Executar", modeIndex: id === "edit-approval" ? 2 : Math.max(0, modes.findIndex(x => x.name === (previous.mode || "Executar"))), dialogOrigin: null, menuIndex: 0, menuDismissed: false,
      editApproval: previous.editApproval || "ask", editPolicyDraft: id === "edit-approval" ? "auto" : previous.editApproval || "ask", editPolicyExpanded: id === "edit-approval",
      planState: ["plan", "plan-collapsed", "plan-expanded", "plan-chat", "review-diff", "command-approval", "validating", "done"].includes(id) ? { status: id === "done" ? "completed" : "active", completed: id === "done" ? 4 : id === "validating" ? 3 : 1 } : null,
      planVisible: ["plan", "plan-collapsed", "plan-expanded", "plan-chat", "review-diff", "command-approval", "validating"].includes(id),
      planExpanded: id !== "plan-collapsed",
      choice: 0, modelIndex: id === "model-fallback" ? 3 : Math.max(0, models.findIndex(x => x.id === previous.model)), modelQuery: "",
      expanded: id === "stages-expanded", reasoningExpanded: id === "stages-expanded", details: false,
      lastDraft: previous.lastDraft || previous.draft || "", lastAnswer: previous.lastAnswer || "", lastQuestion: previous.lastQuestion || "", questionCommand: false,
      tls: id === "provider-custom", providerKind: id === "provider-ollama" ? "ollama" : id === "provider-custom" ? "openai-compatible" : "openai",
      providerName: previous.providerName || "Meu provedor", providerKey: previous.providerKey || "", providerUrl: previous.providerUrl || "", advanced: id === "provider-custom", pauseQuestion: false,
      liveSummary: previous.liveSummary ?? true, defaultMode: previous.defaultMode || "Executar", resumeSession: previous.resumeSession ?? true,
      preferenceOriginal: { liveSummary: previous.liveSummary ?? true, defaultMode: previous.defaultMode || "Executar", resumeSession: previous.resumeSession ?? true },
      toast: "", hint: "", approved: false, actionRejected: false, freeQuestion: "",
    };
  }
  function matches(state) {
    const query = state.draft.trim().toLowerCase();
    return commands.filter(x => x.name.startsWith(query));
  }
  function hasMenu(state) {
    return !state.menuDismissed && /^\/[^\s]*$/.test(state.draft) && (!questions[state.route] || state.questionCommand);
  }
  function filteredModels(state) {
    return models.filter(x => x.id.toLowerCase().includes(state.modelQuery.toLowerCase()));
  }
  function go(state, target, preserve = true) {
    const next = initial(target, state);
    if (preserve) next.lastDraft = state.draft.startsWith("/") ? state.lastDraft : state.draft || state.lastDraft;
    if (target === "welcome") next.draft = next.lastDraft;
    if (state.planState) {
      next.planState = { ...state.planState };
      next.planExpanded = state.planExpanded;
    }
    if (target === "validating" && next.planState?.status === "active") next.planState = { ...next.planState, completed: 3 };
    if (target === "done" && next.planState?.status === "active") next.planState = { status: "completed", completed: 4 };
    next.planVisible = next.planState?.status === "active";
    if (dialogKinds.has(screen(target).kind)) {
      next.dialogOrigin = state.dialogOrigin || (dialogKinds.has(screen(state.route).kind) ? null : { ...state, route: state.route.startsWith("slash") ? "welcome" : state.route, draft: state.questionCommand || state.draft.startsWith("/") && !questions[state.route] ? state.lastDraft : state.draft, menuDismissed: true, questionCommand: false });
      next.providerKind = state.providerKind;
      next.tls = state.tls;
    }
    if (["provider-testing", "provider-success", "provider-error"].includes(target)) {
      next.providerKind = state.providerKind;
      next.tls = state.tls;
      next.advanced = state.advanced;
    }
    return next;
  }
  function finishDialog(state, changes = {}) {
    const next = { ...(state.dialogOrigin || { ...initial("welcome", state), draft: state.lastDraft, planState: state.planState, planVisible: state.planVisible, planExpanded: state.planExpanded }), ...changes, dialogOrigin: null };
    if (state.planState) {
      next.planState = state.planState;
      next.planVisible = state.planState.status === "active";
      next.planExpanded = state.planExpanded;
    }
    if (next.route === "first-run" && changes.model) next.route = "welcome";
    return next;
  }
  function modelDetails(id) {
    const model = models.find(x => x.id === id);
    const total = model?.tokens || 8192;
    const reserve = total > 32768 ? 8192 : 2048;
    const margin = 512;
    const budget = total - reserve - margin;
    const used = Math.min(16000, Math.round(budget * .75));
    return { total, reserve, margin, budget, used, percent: Math.round(used / budget * 100), reported: !!model?.tokens, source: model?.tokens ? "API: contexto do modelo" : "Estimativa conservadora", tools: model?.tools };
  }
  function executionPlan(state) {
    const completed = state.planState?.completed ?? 0;
    const active = state.planState?.status === "active";
    const steps = ["Investigar a autenticação", "Corrigir a validação de usuários", "Cobrir os cenários de permissão", "Executar testes e revisar o resultado"].map((title, index) => ({ title, status: index < completed ? "done" : active && index === completed ? "running" : "pending" }));
    return { steps, completed, current: steps.find(step => step.status === "running")?.title || (state.planState?.status === "cancelled" ? "Cancelado" : "Concluído") };
  }
  function confirmAnswer(state) {
    const q = questions[state.route];
    const answer = state.draft.trim() || q?.choices[state.choice];
    if (!answer) return { ...state, hint: "Escreva sua resposta antes de continuar." };
    if (!state.draft.trim() && state.choice === 2 && state.route !== "ask-text") {
      return { ...go(state, "ask-text"), freeQuestion: state.route === "context-scope" ? "Quais arquivos ou módulos você quer priorizar?" : "Qual abordagem você prefere para a autenticação?" };
    }
    return { ...go(state, "ask-followup", false), lastAnswer: answer, lastQuestion: state.freeQuestion || q.title, draft: "", lastDraft: state.lastDraft };
  }
  function reduce(state, event) {
    let next = { ...state, hint: "" };
    switch (event.type) {
      case "SCREEN": return initial(event.id, state);
      case "DRAFT": return { ...next, draft: event.value, menuIndex: 0, menuDismissed: false };
      case "PALETTE": return { ...next, lastDraft: state.questionCommand || state.draft.startsWith("/") && !questions[state.route] ? state.lastDraft : state.draft || state.lastDraft, draft: "/", questionCommand: true, menuDismissed: false, menuIndex: 0 };
      case "TOGGLE_DETAILS": return { ...next, expanded: !state.expanded };
      case "TOGGLE_PLAN": return { ...next, planExpanded: !state.planExpanded };
      case "START_PLAN": return { ...go(next, "review-diff"), planState: { status: "active", completed: 1 }, planVisible: true, planExpanded: true };
      case "CANCEL_PLAN": return state.planState?.status === "active" ? { ...go(next, "cancelled", false), draft: state.draft, planState: { ...state.planState, status: "cancelled" }, planVisible: false } : next;
      case "PLAN_PROGRESS": {
        if (state.planState?.status !== "active" || !Number.isInteger(event.completed) || event.completed < state.planState.completed || event.completed > 4) return next;
        return { ...next, planState: { status: event.completed === 4 ? "completed" : "active", completed: event.completed }, planVisible: event.completed < 4 };
      }
      case "TOGGLE_REASONING": return { ...next, reasoningExpanded: !state.reasoningExpanded };
      case "TOGGLE_ADVANCED": return { ...next, advanced: !state.advanced };
      case "TOGGLE_SUMMARY": return { ...next, liveSummary: !state.liveSummary };
      case "TLS": return { ...next, tls: event.value };
      case "CHOICE": return { ...next, choice: event.index };
      case "ANSWER": return confirmAnswer(next);
      case "PAUSE_QUESTION": return { ...next, pauseQuestion: true, hint: "A tarefa ficará pausada até você responder." };
      case "MODEL_QUERY": return { ...next, modelQuery: event.value, modelIndex: 0 };
      case "MODEL_SELECT": return { ...next, modelIndex: event.index };
      case "MODEL_USE": {
        const model = filteredModels(state)[state.modelIndex];
        if (!model || model.tools === false) return { ...next, hint: "Selecione um modelo disponível para conversa." };
        return finishDialog(next, { model: model.id, providerKind: state.providerKind, providerName: state.providerName, providerKey: state.providerKey, providerUrl: state.providerUrl, tls: state.tls, toast: "Modelo selecionado" });
      }
      case "PROVIDER_KIND": return { ...next, providerKind: event.value, providerUrl: "", tls: false, route: "provider" };
      case "PROVIDER_FIELD": return ["providerName", "providerKey", "providerUrl"].includes(event.name) ? { ...next, [event.name]: event.value, route: ["provider-success", "provider-error"].includes(state.route) ? "provider" : state.route } : next;
      case "PROVIDER_TEST": return go(next, "provider-testing");
      case "PROVIDER_OK": return go(next, "provider-success");
      case "PROVIDER_CANCEL_TEST": return go(next, "provider");
      case "APPROVE": return { ...go(next, "validating"), draft: state.dialogOrigin?.draft || state.lastDraft, approved: true };
      case "REJECT": return { ...go(next, "cancelled"), actionRejected: true, toast: "Solicitação rejeitada · ação não executada" };
      case "MODE_SELECT": return Number.isInteger(event.index) && modes[event.index] ? { ...next, modeIndex: event.index, editPolicyExpanded: false } : next;
      case "EDIT_POLICY_TOGGLE": return { ...next, editPolicyExpanded: !state.editPolicyExpanded };
      case "EDIT_POLICY_SELECT": return ["ask", "auto"].includes(event.value) ? { ...next, editPolicyDraft: event.value } : next;
      case "MODE_USE": return finishDialog(next, { mode: modes[state.modeIndex].name, editApproval: state.modeIndex === 2 ? state.editPolicyDraft : state.editApproval, toast: `Modo de trabalho: ${modes[state.modeIndex].name}` });
      case "PREFERENCE": return ["defaultMode", "resumeSession", "liveSummary"].includes(event.name) ? { ...next, [event.name]: event.value } : next;
      case "PREFERENCES_SAVE": return finishDialog(next, { defaultMode: state.defaultMode, resumeSession: state.resumeSession, liveSummary: state.liveSummary, toast: "Preferências salvas nesta simulação" });
      case "NEW_SESSION": return { ...initial("welcome", state), mode: state.defaultMode, editApproval: "ask", editPolicyDraft: "ask", draft: "", lastDraft: "", lastAnswer: "", lastQuestion: "", toast: "Nova conversa · histórico anterior preservado" };
      case "BACK": return finishDialog(next, state.route === "preferences" ? state.preferenceOriginal : {});
      case "CANCEL": return { ...go(next, "cancelled", false), draft: state.draft };
      case "GO": return go(next, event.target);
      case "KEY": {
        const key = event.key;
        if (hasMenu(state)) {
          const options = matches(state);
          if (key === "ArrowDown" || key === "ArrowUp") {
            const delta = key === "ArrowDown" ? 1 : -1;
            return { ...next, menuIndex: options.length ? (state.menuIndex + delta + options.length) % options.length : 0 };
          }
          if (key === "Escape") return { ...next, draft: state.questionCommand ? state.lastDraft : state.draft, questionCommand: false, menuDismissed: true };
          if (key === "Tab" && options.length) return { ...next, draft: options[state.menuIndex % options.length].name + " ", menuDismissed: true };
          if (key === "Enter") {
            if (!options.length) return { ...next, hint: "Nenhum comando corresponde ao texto. Esc fecha a lista." };
            return go(next, options[state.menuIndex % options.length].target);
          }
        }
        if (questions[state.route]) {
          const q = questions[state.route];
          if ((key === "ArrowDown" || key === "ArrowUp") && !state.draft && q.choices.length) {
            return { ...next, choice: (state.choice + (key === "ArrowDown" ? 1 : -1) + q.choices.length) % q.choices.length };
          }
          if (key === "Enter") return confirmAnswer(next);
          if (key === "Escape") return { ...next, pauseQuestion: true };
        }
        if (key === "Escape" && screen(state.route).kind !== "chat" && !["welcome", "first-run"].includes(state.route)) return reduce(next, { type: "BACK" });
        if (key === "Enter" && state.draft.trim() && !busy.has(state.route)) {
          return { ...go(next, "thinking", false), question: state.draft.trim(), draft: "" };
        }
        return next;
      }
      default: return next;
    }
  }
  globalThis.CodaroPrototype = { screens, commands, models, modes, busy, questions, screen, initial, matches, hasMenu, filteredModels, modelDetails, executionPlan, reduce };
})();
