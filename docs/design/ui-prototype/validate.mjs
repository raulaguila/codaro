// Validates the standalone proposal, not the production agent or provider APIs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = fileURLToPath(new URL(".", import.meta.url));
const source = name => readFileSync(root + name, "utf8");
const context = vm.createContext({});
vm.runInContext(source("core.js"), context);
const C = context.CodaroPrototype;
const key = (state, value) => C.reduce(state, { type: "KEY", key: value });
let checks = 0;
function test(name, fn) { fn(); checks++; console.log(`✓ ${name}`); }

test("Setas e Enter abrem o comando destacado", () => {
  let s = C.initial("slash");
  s = key(s, "ArrowDown");
  assert.equal(key(s, "Enter").route, "mode");
  assert.equal(key(key(C.initial("slash"), "ArrowUp"), "Enter").route, "help");
});
test("Filtrar comandos mantém navegação e seleção", () => {
  const s = C.initial("slash-filter");
  assert.equal(C.matches(s).length, 2);
  assert.equal(key(key(s, "ArrowDown"), "Enter").route, "mode");
});
test("Tab completa sem executar; Esc preserva o texto", () => {
  let s = key(C.initial("slash-filter"), "Tab");
  assert.equal(s.draft, "/models ");
  assert.equal(s.route, "slash-filter");
  s = key(C.initial("slash-filter"), "Escape");
  assert.equal(s.draft, "/mo");
  assert.equal(C.hasMenu(s), false);
});
test("Comando desconhecido não vira mensagem ao modelo", () => {
  const s = key(C.initial("slash-empty"), "Enter");
  assert.equal(s.route, "slash-empty");
  assert.equal(s.draft, "/xyz");
});
test("Modelo selecionado restaura rascunho anterior à configuração", () => {
  let s = { ...C.initial("slash"), lastDraft: "Revise minhas alterações" };
  s = key(s, "Enter");
  s = C.reduce(s, { type: "MODEL_SELECT", index: 1 });
  s = C.reduce(s, { type: "MODEL_USE" });
  assert.equal(s.model, "qwen2.5-coder:7b");
  assert.equal(s.draft, "Revise minhas alterações");
});
test("Escolher opção não confirma nem autoriza alterações", () => {
  const s = C.reduce(C.initial("ask-choice"), { type: "CHOICE", index: 1 });
  assert.equal(s.route, "ask-choice");
  assert.equal(s.approved, false);
  const done = C.reduce(s, { type: "ANSWER" });
  assert.equal(done.lastAnswer, "Pode alterar o contrato");
  assert.equal(done.approved, false);
});
test("Resposta livre prevalece sobre a opção destacada", () => {
  let s = C.reduce(C.initial("ask-choice"), { type: "DRAFT", value: "Preserve também o endpoint v1" });
  s = key(s, "Enter");
  assert.equal(s.lastAnswer, "Preserve também o endpoint v1");
  assert.equal(s.lastQuestion, C.questions["ask-choice"].title);
});
test("Resposta que começa com / é enviada como resposta à pergunta", () => {
  const s = C.reduce(C.initial("ask-text"), { type: "DRAFT", value: "/api/v1" });
  assert.equal(C.hasMenu(s), false);
  assert.equal(key(s, "Enter").lastAnswer, "/api/v1");
});
test("Pergunta obrigatória pode pausar; não continua sem resposta", () => {
  const s = key(C.initial("ask-text"), "Enter");
  assert.equal(s.route, "ask-text");
  assert.ok(s.hint);
  assert.equal(key(s, "Escape").route, "ask-text");
  assert.equal(key(s, "Escape").pauseQuestion, true);
});
test("Outra abordagem abre resposta livre específica", () => {
  let s = C.reduce(C.initial("ask-choice"), { type: "CHOICE", index: 2 });
  s = key(s, "Enter");
  assert.equal(s.route, "ask-text");
  assert.match(s.freeQuestion, /abordagem/);
});
test("Modelo sem conversa não pode ser ativado", () => {
  let s = C.reduce(C.initial("models"), { type: "MODEL_QUERY", value: "embedding" });
  s = C.reduce(s, { type: "MODEL_USE" });
  assert.equal(s.route, "models");
  assert.equal(s.model, "qwen3.8:27b");
});
test("Teste do provedor preserva formulário e TLS", () => {
  let s = C.initial("provider-custom");
  s = C.reduce(s, { type: "PROVIDER_FIELD", name: "providerUrl", value: "https://demo.example/v1" });
  s = C.reduce(s, { type: "PROVIDER_FIELD", name: "providerKey", value: "ficticia" });
  s = C.reduce(s, { type: "PROVIDER_TEST" });
  s = C.reduce(s, { type: "PROVIDER_OK" });
  assert.equal(s.providerKind, "openai-compatible");
  assert.equal(s.providerUrl, "https://demo.example/v1");
  assert.equal(s.providerKey, "ficticia");
  assert.equal(s.tls, true);
});
test("Aprovação é explícita e rejeição não a concede", () => {
  assert.equal(C.initial("review-diff").approved, false);
  assert.equal(C.reduce(C.initial("review-diff"), { type: "APPROVE" }).approved, true);
  const rejected = C.reduce(C.initial("review-diff"), { type: "REJECT" });
  assert.equal(rejected.approved, false);
  assert.equal(rejected.route, "cancelled");
});
test("Detalhes recolhidos não reabrem ao editar o rascunho", () => {
  let s = C.reduce(C.initial("stages-expanded"), { type: "TOGGLE_DETAILS" });
  s = C.reduce(s, { type: "DRAFT", value: "Próxima tarefa" });
  assert.equal(s.expanded, false);
  assert.equal(s.liveSummary, true);
});
test("Modo só muda após confirmação e preserva conversa e rascunho", () => {
  const original = { ...C.initial("conversation"), draft: "Revise os testes" };
  let s = C.reduce(original, { type: "GO", target: "mode" });
  s = C.reduce(s, { type: "MODE_SELECT", index: 1 });
  assert.equal(s.mode, "Executar");
  assert.equal(s.route, "mode");
  s = C.reduce(s, { type: "MODE_USE" });
  assert.equal(s.mode, "Planejar");
  assert.equal(s.route, "conversation");
  assert.equal(s.draft, "Revise os testes");
});
test("Trocar modelo mantém o turno e atualiza o orçamento de contexto", () => {
  let s = { ...C.initial("conversation"), draft: "Continue a revisão" };
  s = C.reduce(s, { type: "GO", target: "models" });
  s = C.reduce(s, { type: "MODEL_SELECT", index: 1 });
  s = C.reduce(s, { type: "MODEL_USE" });
  assert.equal(s.route, "conversation");
  assert.equal(s.draft, "Continue a revisão");
  const info = C.modelDetails(s.model);
  assert.equal(info.total, 32768);
  assert.equal(info.budget, 32768 - info.reserve - info.margin);
  assert.equal(info.used, 16000);
  assert.equal(C.modelDetails("modelo-sem-metadados").reported, false);
});
test("Fluxo de provedor e modelos preserva a pergunta pendente", () => {
  let s = { ...C.initial("ask-text"), draft: "Mantenha o endpoint /v1" };
  s = C.reduce(s, { type: "GO", target: "provider" });
  s = C.reduce(s, { type: "PROVIDER_KIND", value: "openai-compatible" });
  s = C.reduce(s, { type: "TLS", value: true });
  s = C.reduce(s, { type: "PROVIDER_FIELD", name: "providerName", value: "Servidor de teste" });
  s = C.reduce(s, { type: "PROVIDER_TEST" });
  s = C.reduce(s, { type: "PROVIDER_OK" });
  s = C.reduce(s, { type: "GO", target: "models" });
  s = C.reduce(s, { type: "MODEL_USE" });
  assert.equal(s.route, "ask-text");
  assert.equal(s.draft, "Mantenha o endpoint /v1");
  assert.equal(s.providerKind, "openai-compatible");
  assert.equal(s.providerName, "Servidor de teste");
  assert.equal(s.tls, true);
});
test("Preferências só são aplicadas ao salvar", () => {
  const original = { ...C.initial("conversation"), draft: "Meu rascunho" };
  let s = C.reduce(original, { type: "GO", target: "preferences" });
  s = C.reduce(s, { type: "PREFERENCE", name: "liveSummary", value: false });
  const cancelled = C.reduce(s, { type: "BACK" });
  assert.equal(cancelled.liveSummary, true);
  assert.equal(cancelled.route, "conversation");
  s = C.reduce(s, { type: "PREFERENCE", name: "defaultMode", value: "Planejar" });
  s = C.reduce(s, { type: "PREFERENCES_SAVE" });
  assert.equal(s.liveSummary, false);
  assert.equal(s.route, "conversation");
  assert.equal(s.draft, "Meu rascunho");
  assert.equal(C.reduce(s, { type: "NEW_SESSION" }).mode, "Planejar");
});
test("Cancelar o teste de conexão retorna ao formulário preenchido", () => {
  let s = C.initial("provider-custom");
  s = C.reduce(s, { type: "PROVIDER_FIELD", name: "providerUrl", value: "https://example.test/v1" });
  s = C.reduce(s, { type: "PROVIDER_TEST" });
  s = C.reduce(s, { type: "PROVIDER_CANCEL_TEST" });
  assert.equal(s.route, "provider");
  assert.equal(s.providerUrl, "https://example.test/v1");
  assert.equal(s.tls, true);
});
test("Comandos durante execução e Esc preservam o rascunho", () => {
  let s = { ...C.initial("thinking"), draft: "Próximo pedido" };
  s = C.reduce(s, { type: "PALETTE" });
  assert.equal(C.hasMenu(s), true);
  assert.equal(key(s, "Escape").draft, "Próximo pedido");
  s = { ...C.initial("ask-text"), draft: "/api/v1" };
  s = C.reduce(s, { type: "PALETTE" });
  assert.equal(key(s, "Escape").draft, "/api/v1");
});
test("Cancelar seleção do modo preserva pergunta pendente e modo atual", () => {
  const original = { ...C.initial("ask-text"), draft: "Preserve a API v1" };
  let s = C.reduce(original, { type: "GO", target: "mode" });
  s = C.reduce(s, { type: "MODE_SELECT", index: 0 });
  s = key(s, "Escape");
  assert.equal(s.route, "ask-text");
  assert.equal(s.mode, "Executar");
  assert.equal(s.draft, "Preserve a API v1");
});
test("Aprovação automática de edições exige aplicar e não autoriza comandos", () => {
  const original = C.initial("conversation");
  let s = C.reduce(original, { type: "GO", target: "mode" });
  s = C.reduce(s, { type: "EDIT_POLICY_SELECT", value: "auto" });
  assert.equal(s.editApproval, "ask");
  assert.equal(C.reduce(s, { type: "BACK" }).editApproval, "ask");
  s = C.reduce(s, { type: "MODE_USE" });
  assert.equal(s.editApproval, "auto");
  const command = C.reduce(s, { type: "GO", target: "command-approval" });
  assert.equal(command.approved, false);
  assert.equal(command.route, "command-approval");
  assert.equal(C.reduce(s, { type: "NEW_SESSION" }).editApproval, "ask");
});
test("Plano mantém recolhimento manual e representa progresso até a conclusão", () => {
  let s = C.initial("plan-expanded");
  assert.equal(s.planVisible, true);
  s = C.reduce(s, { type: "TOGGLE_PLAN" });
  s = C.reduce(s, { type: "DRAFT", value: "Minha próxima mensagem" });
  s = C.reduce(s, { type: "GO", target: "mode" });
  s = C.reduce(s, { type: "MODE_USE" });
  assert.equal(s.planExpanded, false);
  assert.equal(s.draft, "Minha próxima mensagem");
  assert.equal(C.executionPlan(s).completed, 1);
  s = C.reduce(s, { type: "GO", target: "validating" });
  assert.equal(C.executionPlan(s).completed, 3);
  const done = C.executionPlan(C.reduce(s, { type: "GO", target: "done" }));
  assert.equal(done.completed, done.steps.length);
  assert.ok(done.steps.every(step => step.status === "done"));
});
test("Plano ativo permanece no input até concluir ou cancelar explicitamente", () => {
  let s = C.initial("plan-expanded");
  assert.equal(s.planExpanded, true);
  for (const route of ["conversation", "ask-choice", "answer", "connection-error", "welcome"]) {
    s = C.reduce(s, { type: "GO", target: route });
    assert.equal(s.planState.status, "active");
    assert.equal(s.planVisible, true);
    assert.equal(C.executionPlan(s).completed, 1);
  }
  s = C.reduce(s, { type: "CANCEL" });
  assert.equal(s.planVisible, true);
  s = C.reduce(s, { type: "CANCEL_PLAN" });
  assert.equal(s.planState.status, "cancelled");
  assert.equal(s.planVisible, false);
  assert.equal(C.reduce(s, { type: "PLAN_PROGRESS", completed: 4 }).planState.status, "cancelled");
});
test("Conclusão remove o plano do input e preserva seu resultado", () => {
  let s = C.initial("plan-expanded");
  s = C.reduce(s, { type: "PLAN_PROGRESS", completed: 2 });
  s = C.reduce(s, { type: "GO", target: "mode" });
  s = C.reduce(s, { type: "PLAN_PROGRESS", completed: 3 });
  s = C.reduce(s, { type: "BACK" });
  assert.equal(C.executionPlan(s).completed, 3);
  s = C.reduce(s, { type: "PLAN_PROGRESS", completed: 4 });
  assert.equal(s.planState.status, "completed");
  assert.equal(s.planVisible, false);
  assert.equal(C.executionPlan(s).completed, 4);
});
test("Durante execução Enter preserva o próximo rascunho", () => {
  const s = { ...C.initial("thinking"), draft: "Próximo pedido" };
  assert.equal(key(s, "Enter").route, "thinking");
  assert.equal(C.reduce(s, { type: "CANCEL" }).draft, "Próximo pedido");
});

// Lightweight DOM harness: executes actual UI bindings. It does not measure layout.
const nodes = new Map(), listeners = new Map(), timers = new Map();
let timerId = 0;
let document;
function node(id = "") {
  return {
    id, value: "", innerHTML: "", textContent: "", style: {}, dataset: {}, selectionStart: 0, selectionEnd: 0,
    classList: { add() {}, toggle() {} },
    focus() { document.activeElement = this; },
    setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
    setRangeText(value, a, b) { this.value = this.value.slice(0, a) + value + this.value.slice(b); this.selectionStart = this.selectionEnd = a + value.length; },
    addEventListener(type, fn) { listeners.set(id + ":" + type, fn); },
    appendChild() {}, querySelector() { return null; }, querySelectorAll() { return []; }, closest() { return null; }, scrollIntoView() {},
  };
}
document = {
  body: node("body"), activeElement: null,
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id); },
  createElement() { return node(); },
  addEventListener(type, fn) { listeners.set("document:" + type, fn); },
};
Object.assign(context, {
  document, URLSearchParams, location: { hash: "", search: "" }, history: { replaceState() {} },
  window: { addEventListener() {} },
  setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
  clearTimeout(id) { timers.delete(id); },
});
vm.runInContext(source("ui.js"), context);
const UI = context.CodaroPrototypeUI;
function press(key, target = document.getElementById("draft"), extra = {}) {
  let prevented = false;
  listeners.get("document:keydown")({ key, target, preventDefault() { prevented = true; }, ...extra });
  return prevented;
}
test("Bindings reais: /, setas, Enter abrem o seletor correto", () => {
  UI.dispatch({ type: "SCREEN", id: "slash-filter" });
  assert.equal(press("ArrowDown"), true);
  press("Enter");
  assert.equal(UI.getState().route, "mode");
});
test("Bindings reais: setas destacam modo e Enter confirma", () => {
  UI.dispatch({ type: "SCREEN", id: "mode" });
  const target = { closest: selector => selector === ".mode-list" ? {} : null };
  press("ArrowUp", target);
  assert.equal(UI.getState().modeIndex, 1);
  assert.equal(UI.getState().mode, "Executar");
  press("Enter", target);
  assert.equal(UI.getState().mode, "Planejar");
  UI.dispatch({ type: "SCREEN", id: "mode" });
  UI.dispatch({ type: "MODE_SELECT", index: 2 });
  UI.dispatch({ type: "MODE_USE" });
});
test("Menu de modos preserva conversa visível e concentra a confirmação em Aplicar", () => {
  UI.dispatch({ type: "SCREEN", id: "conversation" });
  UI.dispatch({ type: "GO", target: "mode" });
  let html = document.getElementById("terminal").innerHTML;
  assert.ok(html.includes('class="mode-popover"'));
  assert.ok(!html.includes('class="modal-backdrop"'));
  assert.ok(html.includes("Como funciona a autenticação?"));
  assert.equal((html.match(/data-action="MODE_USE"/g) || []).length, 1);
  assert.ok(html.includes("Edições: pedir autorização"));
  UI.dispatch({ type: "MODE_SELECT", index: 0 });
  html = document.getElementById("terminal").innerHTML;
  assert.ok(!html.includes('class="edit-policy-toggle"'));
  assert.equal(UI.getState().mode, "Executar");
  UI.dispatch({ type: "BACK" });
});
test("Bindings reais: Ctrl+P preserva rascunho ao escolher modelo", () => {
  UI.dispatch({ type: "SCREEN", id: "welcome" });
  UI.dispatch({ type: "DRAFT", value: "Não perca este pedido" });
  press("p", undefined, { ctrlKey: true });
  press("Enter");
  UI.dispatch({ type: "MODEL_USE" });
  assert.equal(UI.getState().draft, "Não perca este pedido");
});
test("Bindings reais: Alt+Enter insere linha, sem enviar", () => {
  const draft = document.getElementById("draft");
  draft.value = "primeira segunda";
  draft.selectionStart = 8; draft.selectionEnd = 9;
  press("Enter", draft, { altKey: true });
  assert.equal(UI.getState().draft, "primeira\nsegunda");
  assert.equal(UI.getState().route, "welcome");
});
test("Composição de texto não envia a mensagem antes da confirmação", () => {
  UI.dispatch({ type: "SCREEN", id: "welcome" });
  UI.dispatch({ type: "DRAFT", value: "Texto em composição" });
  assert.equal(press("Enter", undefined, { isComposing: true }), false);
  assert.equal(UI.getState().route, "welcome");
  assert.equal(UI.getState().draft, "Texto em composição");
});
test("Cancelar teste remove o resultado pendente do provedor", () => {
  UI.dispatch({ type: "SCREEN", id: "provider-custom" });
  UI.dispatch({ type: "PROVIDER_TEST" });
  assert.equal(timers.size, 1);
  UI.dispatch({ type: "BACK" });
  assert.equal(timers.size, 0);
  assert.equal(UI.getState().route, "welcome");
});
test("Texto livre é escapado antes de entrar no HTML", () => {
  UI.dispatch({ type: "SCREEN", id: "welcome" });
  UI.dispatch({ type: "DRAFT", value: '<img src=x onerror="alert(1)">' });
  press("Enter");
  const html = document.getElementById("terminal").innerHTML;
  assert.ok(html.includes("&lt;img"));
  assert.ok(!html.includes('<img src=x onerror="alert(1)">'));
});
test("Pergunta e único input compartilham componente; resposta entra no histórico", () => {
  UI.dispatch({ type: "SCREEN", id: "ask-choice" });
  let html = document.getElementById("terminal").innerHTML;
  assert.ok(html.indexOf('class="question-dock"') > html.indexOf('class="composer answering"'));
  assert.ok(html.includes('class="response-card" role="group" aria-labelledby="pending-question"'));
  assert.equal((html.match(/id="draft"/g) || []).length, 1);
  assert.ok(html.includes('data-action="ANSWER"'));
  assert.ok(!html.includes('data-action="FREE_ANSWER"'));
  UI.dispatch({ type: "DRAFT", value: "Preserve a API pública" });
  press("Enter");
  html = document.getElementById("terminal").innerHTML;
  assert.ok(!html.includes('class="question-dock"'));
  assert.ok(html.includes("Podemos alterar o contrato da autenticação?"));
  assert.ok(html.includes("Preserve a API pública"));
});
test("Conversa e pergunta compartilham input com plano fixo e recolhível", () => {
  UI.dispatch({ type: "SCREEN", id: "plan-chat" });
  let html = document.getElementById("terminal").innerHTML;
  assert.ok(html.includes('aria-label="Plano de execução ativo"'));
  assert.ok(html.includes('id="execution-plan-details"'));
  assert.ok(html.includes("Como funciona a autenticação?"));
  UI.dispatch({ type: "GO", target: "ask-choice" });
  html = document.getElementById("terminal").innerHTML;
  assert.ok(html.includes('class="question-dock"'));
  assert.ok(html.includes('aria-label="Plano de execução ativo"'));
  assert.equal((html.match(/id="draft"/g) || []).length, 1);
  UI.dispatch({ type: "TOGGLE_PLAN" });
  html = document.getElementById("terminal").innerHTML;
  assert.ok(!html.includes('id="execution-plan-details"'));
  assert.ok(html.includes('aria-label="Plano de execução ativo"'));
  UI.dispatch({ type: "CANCEL_PLAN" });
  html = document.getElementById("terminal").innerHTML;
  assert.ok(!html.includes('aria-label="Plano de execução ativo"'));
  assert.ok(html.includes("Plano cancelado"));
});
test("Todos os estados renderizam com os bindings reais", () => {
  for (const screen of C.screens) {
    UI.dispatch({ type: "SCREEN", id: screen.id });
    assert.equal(UI.getState().route, screen.id);
    assert.ok(document.getElementById("terminal").innerHTML.length > 500);
  }
});
console.log(`\n${checks} verificações passaram; ${C.screens.length} estados renderizados. Validação visual nativa pendente.`);
