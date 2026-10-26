"""Build the evidence index without changing the approved prototype or application."""

# Prose paragraphs and the standalone HTML template retain their natural line boundaries.
# ruff: noqa: E501

import copy
import json
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

OUT = Path(__file__).parent
ROOT = OUT.parents[2]
PROTO = ROOT / "docs/design/ui-prototype"

# Assessment is deliberately qualitative, never a pixel-fidelity percentage.
ASSESSMENTS = {
    "welcome": (
        "Aderente",
        "P3",
        "Corpo limpo, cabeçalho compacto e campo pronto. Refinar somente alinhamento e respiro.",
    ),
    "first-run": (
        "Parcial",
        "P2",
        "Abre diretamente o cadastro; falta a introdução breve com a única ação Conectar provedor.",
    ),
    "slash": (
        "Parcial",
        "P2",
        "Setas, Enter e Esc funcionam. Lista começa por opções avançadas e repete instruções no rodapé; o protótipo prioriza ações frequentes.",
    ),
    "slash-filter": (
        "Aderente",
        "P3",
        "Filtro /mo, navegação e Enter abrem o seletor destacado; Tab permite completar.",
    ),
    "slash-empty": (
        "Ausente na forma prevista",
        "P2",
        "Sem correspondências, a lista desaparece. Falta o estado Nenhum comando encontrado junto ao input; o comando desconhecido é tratado localmente.",
    ),
    "thinking": (
        "Parcial",
        "P2",
        "Uma seção Etapas com resumo. Analisando o projeto é genérico; não informa a etapa investigada com a precisão do protótipo.",
    ),
    "tool-running": (
        "Aderente",
        "P3",
        "Ação atual e última ação concluída têm pouco peso, com detalhes recolhidos.",
    ),
    "stages-expanded": (
        "Parcial",
        "P2",
        "Atividade e raciocínio estão separados. Há uma camada extra de Collapsible por geração; reduzir o excesso de títulos e recuos.",
    ),
    "streaming": (
        "Aderente",
        "P3",
        "Conteúdo incremental aparece com identificação Codaro e fora dos detalhes de raciocínio.",
    ),
    "answer": (
        "Aderente",
        "P3",
        "Resposta final ocupa sua própria área; não fica no card de pensamento.",
    ),
    "conversation": (
        "Parcial",
        "P2",
        "Turnos têm rótulos e hierarquia, mas faltam resumo compacto de atividades passadas e espaçamento padronizado entre turnos.",
    ),
    "ask-choice": (
        "Parcial",
        "P1",
        "Pergunta, opções e input compartilham componente. Rodapé continua genérico; decisão não reaparece no histórico após retomar a sessão.",
    ),
    "ask-text": (
        "Parcial",
        "P2",
        "Usa o mesmo input e permite texto livre. Falta orientação contextual de envio/pausa e estado visual específico de pergunta pausada.",
    ),
    "ask-followup": (
        "Parcial",
        "P1",
        "Decisão aparece ao enviar, mas não é identificada como Você · decisão e não é restaurada com a sessão.",
    ),
    "plan": (
        "Parcial",
        "P2",
        "Plano pronto tem Executar plano no painel fixo. Faltam a apresentação para revisão e a ação Ajustar o plano do protótipo; /plan troca de modo.",
    ),
    "plan-collapsed": (
        "Aderente",
        "P3",
        "Progresso e etapa atual continuam visíveis junto ao input com lista recolhida.",
    ),
    "plan-expanded": (
        "Aderente",
        "P3",
        "Lista fixa, estados por etapa e rolagem própria. Cabeçalho pode truncar nomes longos.",
    ),
    "plan-chat": (
        "Aderente",
        "P3",
        "Plano fica fora da rolagem da conversa e junto ao input; convive com múltiplos turnos.",
    ),
    "review-diff": (
        "Parcial",
        "P2",
        "Revisão explícita funciona. Diff usa monokai, modal muito alto e ações em ordem/ênfase diferentes; falta agrupamento visual por arquivo e contagem de linhas.",
    ),
    "command-approval": (
        "Aderente",
        "P3",
        "Comando, diretório, timeout e escopo da autorização são claros. Esc/rejeição são seguros.",
    ),
    "validating": (
        "Parcial",
        "P2",
        "Execução dos testes aparece como atividade. Falta uma apresentação consistente de validação e vínculo visível com os resultados ao concluir.",
    ),
    "done": (
        "Parcial",
        "P2",
        "Finalização existe, mas recibo e atalhos Ver alterações/Nova conversa não são um componente estruturado. Mostrar somente validações realmente realizadas.",
    ),
    "provider": (
        "Parcial",
        "P2",
        "BYOK e teste existem. Select/Input de três linhas, formulário alto e três botões deixam a composição mais pesada que a proposta.",
    ),
    "provider-custom": (
        "Parcial",
        "P2",
        "URL e TLS disponíveis, porém dentro do Avançado junto a campos secundários. Falta explicar ao lado da opção que a identidade do servidor não será verificada.",
    ),
    "provider-ollama": (
        "Parcial",
        "P2",
        "URL local configurável e chave opcional. A chave permanece como campo visível; URL e perfil/teste avançado disputam espaço.",
    ),
    "provider-testing": (
        "Parcial",
        "P2",
        "Há progresso, bloqueio dos campos e cancelamento. Usa o campo de erro também como status e muda o significado de Testar conexão conforme o modelo escolhido.",
    ),
    "provider-success": (
        "Parcial",
        "P2",
        "Resultado do catálogo é explícito, mas abre Avançado e aumenta densidade; separar sucesso de catálogo e teste opcional de inferência.",
    ),
    "provider-error": (
        "Parcial",
        "P2",
        "Erro mantém dados e permite tentar novamente. Texto técnico está no mesmo componente dos estados de sucesso/progresso, sem hierarquia própria.",
    ),
    "models": (
        "Parcial",
        "P2",
        "Busca e teclado existem. Select/Input grandes e números sem compactação; incompatíveis são ocultados, em vez de aparecerem desabilitados com motivo.",
    ),
    "models-loading": (
        "Parcial",
        "P2",
        "Seleção é preservada e controles bloqueiam. Catálogo anterior continua parecendo normal; carregamento aparece só no rodapé do modal.",
    ),
    "models-empty": (
        "Parcial",
        "P2",
        "Usar modelo fica desabilitado corretamente. Mensagem fica abaixo de uma área vazia, em vez de ocupar a própria lista.",
    ),
    "model-fallback": (
        "Parcial",
        "P1",
        "Lista mostra ? tokens, mas detalhe apresenta Contexto: 16,384 sem identificar fallback. Origem só no tooltip, pouco acessível pelo teclado.",
    ),
    "model-tools": (
        "Ausente na forma prevista",
        "P2",
        "Não há ação nem resultado do teste de ferramentas no seletor; essa capacidade existe no Avançado do cadastro de provedores.",
    ),
    "preferences": (
        "Parcial",
        "P2",
        "Salva modo, retomada e limites. Falta Mostrar resumos curtos; Retomar automaticamente difere de Oferecer retomada.",
    ),
    "mode": (
        "Aderente",
        "P3",
        "Popover ancorado, conversa ao fundo, três modos e uma ação Aplicar. Preparar próximo modo enquanto executa ainda é bloqueado.",
    ),
    "edit-approval": (
        "Parcial",
        "P2",
        "Política recolhível e confirmação explícita existem. Estado auto vai para o cabeçalho, enquanto o protótipo o coloca na linha de estado.",
    ),
    "status": (
        "Ausente na forma prevista",
        "P2",
        "Comando despeja diagnóstico no histórico do chat. Falta modal chave/valor com detalhes progressivos e Fechar.",
    ),
    "help": (
        "Ausente na forma prevista",
        "P2",
        "Ajuda é um bloco longo no histórico; faltam modal curto e prioridade aos atalhos da interação atual.",
    ),
    "context-compacting": (
        "Parcial",
        "P2",
        "Há eventos de compactação automática e /compact local. Falta a apresentação transitória completa da proposta; /compact mostra contagem de caracteres no chat.",
    ),
    "connection-error": (
        "Parcial",
        "P2",
        "Recuperação oferece Repetir e Configurar. Falta explicar preservação do rascunho e oferecer Trocar modelo nesse estado; botões são grandes.",
    ),
    "auth-error": (
        "Parcial",
        "P2",
        "Direciona à configuração, mas mostra identificador técnico do provedor e diagnóstico sem separar detalhes; rodapé retorna Pronto apesar da falha.",
    ),
    "cancelled": (
        "Parcial",
        "P2",
        "Distingue cancelamento de erro e mantém plano. Há apenas Repetir no card; faltam Revisar e retomar e Ver alterações.",
    ),
    "resumed": (
        "Parcial",
        "P1",
        "Retoma mensagem inicial/final e plano persistido, mas decisões intermediárias da pergunta somem da conversa restaurada.",
    ),
    "no-results": (
        "Parcial",
        "P2",
        "Resposta textual orienta o usuário. Atalhos Buscar pelo fluxo/Indicar arquivo dependem de texto gerado; não existem como ações do componente.",
    ),
    "context-scope": (
        "Parcial",
        "P2",
        "O componente de pergunta comporta escolhas de escopo; não há acionamento automático desse fluxo quando o contexto não cabe. Captura é demonstração do componente, não recuperação integrada.",
    ),
    "new-session": (
        "Parcial",
        "P2",
        "Confirmação preserva rascunho ao cancelar e explica fim do plano. Texto denso e campo Objetivo extra destoam da confirmação curta aprovada.",
    ),
}

FINDINGS = [
    (
        "F01",
        "P1",
        "Decisões intermediárias desaparecem da conversa retomada",
        "interactions_extra.py reproduz pergunta → resposta /api/v2 → resposta final sem repetir a decisão → restore_session. O texto da decisão não reaparece. finish salva somente active_question e resposta final; restore_session reconstrói esses dois itens. Isso compromete a conferência das restrições assumidas pelo agente.",
        "Persistir eventos visíveis de pergunta/decisão e restaurá-los em ordem; manter separado o histórico de protocolo enviado ao modelo. Não basta pedir ao modelo que repita a decisão.",
        "src/codaro/ui/app.py:1130, src/codaro/ui/app.py:1360, src/codaro/ui/app.py:852",
    ),
    (
        "F02",
        "P1",
        "Orçamento de fallback parece contexto confirmado",
        "Com modelo sem metadados, a lista informa ? tokens, mas o detalhe mostra Contexto: 16,384. Nenhuma indicação visível explica que o valor é conservador; a origem fica em tooltip. Os dois testes fallback-labelled falharam.",
        "Exibir Contexto não informado pela API e Orçamento provisório: 16k. Disponibilizar origem e ajustes em detalhes acessíveis por teclado.",
        "src/codaro/ui/providers.py:389",
    ),
    (
        "F03",
        "P1",
        "Redimensionamento deixa plano e pergunta ocuparem quase toda a tela",
        "No fluxo 120×35 → 60×20 com plano e pergunta, a variante short-terminal não foi ativada no resize e a área do chat ficou quase sem altura. O input continuou visível, mas o rodapé foi cortado. Abertura direta em tamanho compacto se comporta melhor.",
        "Recalcular os limites no resize efetivo da tela. Reservar altura mínima para conversa, input e ações; rolar pergunta/plano internamente sem esconder o que é necessário responder.",
        "src/codaro/ui/app.py:252, src/codaro/ui/app.py:258, src/codaro/ui/styles.tcss:43",
    ),
    (
        "F04",
        "P2",
        "Rodapé não acompanha perguntas, comandos e execução",
        "Pergunta com opções mantém Enter envia · Alt+Enter nova linha · / comandos. Não apresenta ↑↓ escolher e Esc pausar. O menu / já tem sua própria linha de instruções, deixando uma segunda linha genérica redundante. Pausar muda somente o status, sem rótulo de pergunta pendente.",
        "Derivar uma única linha de ajuda do estado atual: chat, slash, pergunta, pausa ou execução. Atualizar também o rótulo do componente de pergunta.",
        "src/codaro/ui/app.py:295, src/codaro/ui/app.py:394, src/codaro/ui/questions.py",
    ),
    (
        "F05",
        "P2",
        "Menu / difere na prioridade e no estado vazio",
        "A lista começa por settings, integrations, undo-turn e redo; o protótipo começa por modelos/modo/provedores/plano. Em /xyz o menu fecha silenciosamente. Navegação por setas, Enter e Esc passou nos dois tamanhos.",
        "Priorizar ações comuns, mover ações avançadas para busca/ajuda e manter uma linha Nenhum comando encontrado com Esc para fechar.",
        "src/codaro/interaction.py:9, src/codaro/ui/app.py:353",
    ),
    (
        "F06",
        "P2",
        "Formulários ainda usam controles altos e hierarquia antiga",
        "Select, Input e Checkbox do Textual usam três linhas e molduras fortes. Em 80×24, abrir Avançado pode rolar automaticamente para baixo e tirar tipo/chave do enquadramento; são campos acessíveis por rolagem, não necessariamente inacessíveis. A URL obrigatória de um provedor local divide Avançado com nome, TLS e teste.",
        "Separar campos necessários dos opcionais; URL visível para Ollama/compatible, esconder chave em Ollama sem autenticação, reduzir bordas e altura, manter ações fixas e não deslocar o foco ao expandir.",
        "src/codaro/ui/providers.py:60, src/codaro/ui/providers.py:94",
    ),
    (
        "F07",
        "P2",
        "Teste de catálogo e teste de inferência compartilham a mesma ação",
        "Testar conexão consulta catálogo quando não há modelo e faz duas chamadas de inferência quando um modelo é escolhido no Avançado. O custo é explicado, mas o botão não muda de nome. O seletor de modelos não possui diagnóstico de ferramentas.",
        "Manter Testar conexão para catálogo e uma ação separada Testar ferramentas e resposta com custo informado e resultado próprio, também acessível pelo seletor.",
        "src/codaro/ui/providers.py:145, src/codaro/ui/providers.py:366",
    ),
    (
        "F08",
        "P2",
        "Carregamento, vazio e incompatibilidade de modelos precisam de estados próprios",
        "Atualização mantém linhas antigas com aviso no rodapé; busca vazia deixa um retângulo em branco; embeddings são escondidos com contagem técnica. A proposta coloca feedback na lista e permite entender por que um item está indisponível.",
        "Criar estados da lista e detalhes progressivos; mostrar incompatíveis desabilitados com motivo. Manter seleção existente e impedir ativação durante consulta.",
        "src/codaro/ui/providers.py:352, src/codaro/ui/providers.py:443",
    ),
    (
        "F09",
        "P2",
        "Ajuda e status poluem o histórico",
        "Ambos montam Static com classe question na conversa, parecendo texto do usuário. /status expõe None, False, IDs e caminho de sessão junto a dezenas de detalhes.",
        "Usar modais curtos, com chave/valor e Avançado para diagnóstico. Fechar deve voltar ao rascunho e posição de leitura.",
        "src/codaro/ui/app.py:693, src/codaro/ui/app.py:798, src/codaro/ui/app.py:823",
    ),
    (
        "F10",
        "P2",
        "Revisar plano e preparar o próximo modo ainda não seguem o contrato",
        "/plan muda para Planejar; não abre a revisão do plano existente. Plano pronto expõe Executar plano, mas não Ajustar o plano. open_mode_picker retorna enquanto busy, embora a proposta permita preparar o próximo pedido.",
        "Definir uma entrada explícita para revisar/ajustar o plano e permitir escolha de modo futuro sem alterar a tarefa em andamento.",
        "src/codaro/ui/app.py:305, src/codaro/ui/app.py:639, src/codaro/ui/plan.py:63",
    ),
    (
        "F11",
        "P2",
        "Diff, erros e finalização usam padrões diferentes",
        "Diff usa fundo monokai, grande área vazia e botões Aplicar/Rejeitar/Voltar; recuperação usa botões altos e dados técnicos no texto principal; conclusão depende apenas do Markdown gerado. São composições distintas do mesmo fluxo.",
        "Unificar superfície, ações e tipografia; detalhes de erro recolhíveis; recibo final derivado dos resultados reais, com Ver alterações. Preservar o foco seguro atual em Voltar/Rejeitar.",
        "src/codaro/ui/reviews.py:48, src/codaro/ui/recovery.py:25, src/codaro/ui/app.py:1295",
    ),
    (
        "F12",
        "P2",
        "Preferências, onboarding e nova conversa permanecem densos",
        "Falta controle Mostrar resumos curtos; retomada é automática em vez de oferecida. Primeiro acesso abre formulário diretamente. Nova conversa inclui objetivo opcional e explicação extensa.",
        "Adotar as versões curtas aprovadas e deslocar limites para Avançado; não perder informações sobre descarte de propostas e cancelamento do plano.",
        "src/codaro/ui/preferences.py:35, src/codaro/ui/app.py:232, src/codaro/ui/dialogs.py:23",
    ),
    (
        "F13",
        "P2",
        "Alguns estados dependem do modelo ou não estão integrados",
        "Pergunta de escopo pode ser renderizada pela tool, mas a recuperação de contexto não dispara esse fluxo automaticamente. Busca sem resultados e recibo de conclusão não possuem ações estruturadas. /compact reduz histórico local e imprime caracteres; não reproduz a transição aprovada completa.",
        "Conectar recuperação/escopo e ações de acompanhamento a eventos reais. Exigir confirmação humana somente quando houver uma decisão necessária; não fabricar confirmação de validação.",
        "src/codaro/ui/app.py:789, src/codaro/ui/streaming.py:28, src/codaro/ui/recovery.py:39",
    ),
    (
        "F14",
        "P3",
        "Refino visual e orientações ainda divergem",
        "Há camadas extras nos detalhes de raciocínio, botão de modo sempre com fundo, cores fortes no diff, contexto em porcentagem sem numerador e abreviação cortada de /status em 80 colunas. A política auto aparece no cabeçalho em vez da linha de estado.",
        "Padronizar recuos, reduzir uma camada de títulos, tornar números compactos e manter informação de orçamento acessível sem tooltip. Não buscar identidade de pixels entre HTML e grade do terminal.",
        "src/codaro/ui/styles.tcss, src/codaro/ui/streaming.py:21, src/codaro/ui/app.py:285",
    ),
]


def comparison(scene, destination):
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)

    def tag(name):
        return f"{{{ns}}}{name}"

    svg = ET.Element(tag("svg"), width="2200", height="810", viewBox="0 0 2200 810")
    ET.SubElement(svg, tag("rect"), width="2200", height="810", fill="#0b1017")
    for i, (title, path) in enumerate(
        [
            ("Protótipo aprovado · maquete", PROTO / "screens" / (scene + ".svg")),
            ("CLI atual · captura nativa 120×35", OUT / "screens" / (scene + "-120x35.svg")),
        ]
    ):
        label = ET.SubElement(
            svg,
            tag("text"),
            x=str(20 + i * 1100),
            y="30",
            fill="#dfe6ef",
            style="font:20px sans-serif",
        )
        label.text = title
        source = ET.parse(path).getroot()
        frame = ET.SubElement(
            svg,
            tag("svg"),
            x=str(20 + i * 1100),
            y="50",
            width="1060",
            height="740",
            viewBox=source.get("viewBox"),
        )
        for child in source:
            frame.append(copy.deepcopy(child))
    ET.ElementTree(svg).write(destination, encoding="unicode")


def build():
    catalog = json.loads((PROTO / "screens.json").read_text())["screens"]
    assert set(ASSESSMENTS) == {row["id"] for row in catalog}
    observations = json.loads((OUT / "observations.json").read_text())
    assert not any(row.get("error") for row in observations)
    for row in catalog:
        for size in [(120, 35), (80, 24)]:
            assert (OUT / "screens" / f"{row['id']}-{size[0]}x{size[1]}.svg").exists()
    rows = [
        {
            **row,
            "assessment": ASSESSMENTS[row["id"]][0],
            "priority": ASSESSMENTS[row["id"]][1],
            "detail": ASSESSMENTS[row["id"]][2],
        }
        for row in catalog
    ]
    (OUT / "matrix.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    counts = Counter(row["assessment"] for row in rows)
    checks = json.loads((OUT / "interactions.json").read_text()) + json.loads(
        (OUT / "extra-interactions.json").read_text()
    )
    failed = [row for row in checks if not row["passed"]]
    report = [
        "# Comparação completa da CLI com o protótipo aprovado",
        "\nAnálise de 21/10/2026. A base visual está próxima da proposta no chat principal, mas a adoção completa ainda não terminou. As maiores diferenças estão nos formulários, recuperação, ajuda/status e transições entre estados. Há também problemas funcionais de retomada e comunicação do contexto.",
        "\n## Escopo e limites",
        "\nForam mapeados os **46 estados**, com **92 capturas de referência** da aplicação Textual em 120×35 e 80×24, mais duas capturas de pergunta pausada. A verificação adicional cobre Ctrl+P, gerenciamento de provedores, fim de ferramenta, retomada de decisão e resize com pergunta/plano (seis capturas). São **100 capturas nativas**, não imagens geradas por IA.",
        "\nO modelo, os eventos e o catálogo HTTP são sintéticos, em diretórios temporários. O objetivo é verificar renderização e interação da UI, sem inferência real, custos ou credenciais reais. Onde não há fluxo equivalente, a captura mostra a tela existente mais próxima e a matriz o declara. A pergunta de escopo é uma demonstração da capacidade do componente, não prova da recuperação automática. As maquetes SVG são a referência visual; comportamento foi comparado também com core.js, ui.js e README do protótipo. Não houve alteração da aplicação nem do protótipo.",
        "\nFoi usado o renderizador real do Textual em modo headless. Não substitui conferência de fontes, mouse e teclas Meta num terminal físico ou inspeção do protótipo em navegador. Variações inevitáveis de fonte/grade não foram classificadas como bugs.",
        "\n## Resultado",
        "\n"
        + "; ".join(
            f"**{value} {label}**"
            for label, value in [
                ("aderentes", counts["Aderente"]),
                ("parciais", counts["Parcial"]),
                ("ausentes na forma prevista", counts["Ausente na forma prevista"]),
            ]
        )
        + ". A classificação considera o comportamento e a estrutura; não é uma porcentagem de semelhança visual.",
        f"\nVerificações específicas: **{len(checks) - len(failed)}/{len(checks)} atenderam ao esperado**. Falhas de expectativa: "
        + ", ".join(f"`{row['name']}`" for row in failed)
        + ". Dois resultados são o mesmo problema de fallback nos dois tamanhos.",
        "\nTestes existentes da implementação: **9 passaram** em test_prototype_fidelity.py. Referência do protótipo: **37 verificações passaram, 46 estados renderizados**. Esses testes não garantem paridade visual completa — as divergências desta auditoria mostram essa limitação.",
        "\nAbra a [galeria comparativa](gallery.html) para escolher qualquer tela, alternar o tamanho nativo e consultar a diferença lado a lado. [Matriz estruturada](matrix.json), [interações](interactions.json), [interações adicionais](extra-interactions.json) e [geometria](observations.json) preservam a evidência.",
        "\n## O que já está bem resolvido",
        "\n- Início configurado limpo e chat com largura total.\n- Pergunta, escolhas e único input no mesmo componente.\n- Etapas e raciocínio recolhíveis, separados da resposta em streaming/final.\n- Plano fixo junto ao input, com progresso e recolhimento.\n- Popover de modo com uma confirmação Aplicar e conversa ao fundo.\n- Setas/Enter/Esc no menu, resposta literal com / e rascunho preservado ao cancelar nova conversa.\n- Hover do indicador de modo não alterou dimensões no teste realizado.",
        "\n## Achados e correções recomendadas",
    ]
    for identifier, priority, title, evidence, recommendation, source in FINDINGS:
        report += [
            f"\n### {identifier} · {priority} · {title}",
            f"\n{evidence}",
            f"\n**Recomendação:** {recommendation}",
            f"\n**Origem:** {source}.",
        ]
    report += [
        "\n## Matriz de todas as telas",
        "\nAderente = estrutura principal corresponde; ainda pode haver diferenças pontuais. Parcial = tela existe com diferenças relevantes. Ausente na forma prevista = o comando/capacidade pode existir, mas a apresentação/entrada aprovada não foi implementada.",
        "\n| Tela | Avaliação | Prioridade | Diferença e evidência |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        scene = row["id"]
        report.append(
            f"| {row['title']} (`{scene}`) | {row['assessment']} | {row['priority']} | {row['detail']} [CLI](screens/{scene}-120x35.svg) · [80×24](screens/{scene}-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/{scene}.svg) |"
        )
    report += [
        "\n## Sequência sugerida",
        "\n1. **Confiança e legibilidade:** persistência de decisões, fallback explícito e resize com plano/pergunta (F01–F03).\n2. **Interação principal:** rodapé contextual, slash vazio/prioridades, pausa e revisão/ajuste do plano (F04, F05, F10).\n3. **Configuração:** formulários compactos, testes separados, estados do seletor e preferências (F06–F08, F12).\n4. **Consistência do fluxo:** ajuda/status em modal, recuperação, recibo real e refinos (F09, F11, F13, F14).",
        "\nCritério de aceite: comparar novamente os 46 estados; manter chat, input e ações utilizáveis em 80×24 e no resize para 60×20; retomar decisões sem perda; nenhum valor provisório aparentar metadado confirmado; nenhum resultado de teste ser inventado. A reprodução de cada estado deve continuar declarando quando usa fixtures.",
        "\n## Reproduzir",
        "\n```bash\n.venv/bin/python docs/audits/2026-10-21-all-screens-comparison/reproduce.py\n.venv/bin/python docs/audits/2026-10-21-all-screens-comparison/interactions_extra.py\n.venv/bin/python docs/audits/2026-10-21-all-screens-comparison/build_report.py\n.venv/bin/pytest -q tests/test_prototype_fidelity.py\nnode docs/design/ui-prototype/validate.mjs\n```",
        "\nA captura de carregamento solta o transporte simulado e aguarda sua conclusão antes de desmontar a aplicação. Uma primeira execução do harness desmontou antes do fim da atualização e produziu NoMatches no finally; isso não foi tratado como falha comprovada no fluxo do usuário. A reprodução final, com o worker concluído, não tem erros de captura.",
    ]
    (OUT / "README.md").write_text("\n".join(report) + "\n")
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    document = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Codaro · comparação de 46 telas</title>
<style>body{margin:0;background:#111821;color:#dfe6ef;font:15px system-ui}header,main{padding:20px}header{border-bottom:1px solid #314354}h1{font-size:22px;margin:0 0 8px}p{color:#aebdcd;line-height:1.5}select,button,a{font:inherit;color:#dfe6ef}select,button{background:#17212d;border:1px solid #435969;padding:8px;border-radius:6px}label{margin-right:16px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}img{width:100%;background:#111821;border:1px solid #314354}.tag{color:#e5bd78}a{color:#83d6d7}.links{display:flex;gap:20px}#detail{min-height:3em}@media(max-width:800px){.pair{grid-template-columns:1fr}}</style>
<header><h1>Codaro · protótipo aprovado × implementação</h1><p>46 estados · captura real Textual com eventos e HTTP sintéticos. Sem inferência real. Estados ausentes mostram a tela equivalente mais próxima.</p><label>Tela <select id="scene"></select></label><label>CLI <select id="size"><option>120x35</option><option>80x24</option></select></label><button id="prev">← Anterior</button> <button id="next">Próxima →</button></header>
<main><h2 id="title"></h2><span class="tag" id="assessment"></span><p id="detail"></p><div class="pair"><section><h3>Referência · maquete do protótipo</h3><a id="proto-link"><img id="proto" alt="Maquete aprovada"></a></section><section><h3>Implementação · captura nativa</h3><a id="native-link"><img id="native" alt="Captura real Textual"></a></section></div><p class="links"><a href="README.md">Relatório completo e prioridades</a><a href="matrix.json">Matriz JSON</a><a id="text">Texto capturado</a></p></main>
<script>const rows=DATA;const scene=document.getElementById('scene'),size=document.getElementById('size');rows.forEach((r,i)=>{const o=new Option(r.group+' · '+r.title,i);scene.add(o)});function draw(){const r=rows[+scene.value];document.getElementById('title').textContent=r.title;document.getElementById('assessment').textContent=r.assessment+' · '+r.priority;document.getElementById('detail').textContent=r.detail;const p='../../design/ui-prototype/screens/'+r.id+'.svg',n='screens/'+r.id+'-'+size.value;document.getElementById('proto').src=p;document.getElementById('native').src=n+'.svg';document.getElementById('proto-link').href=p;document.getElementById('native-link').href=n+'.svg';document.getElementById('text').href=n+'.txt';location.hash=r.id}scene.onchange=size.onchange=draw;document.getElementById('prev').onclick=()=>{scene.value=(+scene.value+rows.length-1)%rows.length;draw()};document.getElementById('next').onclick=()=>{scene.value=(+scene.value+1)%rows.length;draw()};const initial=rows.findIndex(r=>r.id===location.hash.slice(1));scene.value=initial<0?0:initial;draw();</script></html>""".replace(
        "DATA", data
    )
    (OUT / "gallery.html").write_text(document)
    with tempfile.TemporaryFile() as log:
        for scene in ["ask-choice", "provider", "models", "status"]:
            destination = OUT / f"comparison-{scene}.svg"
            comparison(scene, destination)
            subprocess.run(
                [
                    "inkscape",
                    str(destination),
                    "--export-type=png",
                    "--export-filename=" + str(destination.with_suffix(".png")),
                ],
                check=True,
                stdout=log,
                stderr=log,
            )
    print(dict(counts))
    print(f"{len(checks)} checks; {len(failed)} expectations unmet; 46 scenarios indexed")


if __name__ == "__main__":
    build()
