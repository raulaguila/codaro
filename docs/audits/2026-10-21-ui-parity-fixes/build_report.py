"""Index the post-correction captures; preserve the original audit as evidence."""

# Prose and embedded HTML keep natural paragraph boundaries.
# ruff: noqa: E501

import importlib.util
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).parent
BEFORE = OUT.parent / "2026-10-21-all-screens-comparison"
ROOT = OUT.parents[2]
PROTO = ROOT / "docs/design/ui-prototype"

CORRECTIONS = [
    (
        "F01",
        "Decisões persistidas imediatamente, redigidas como decisões e restauradas; JSON antigo continua compatível. Metadados visuais não são enviados como protocolo de ferramentas.",
        "ask-followup",
    ),
    (
        "F02",
        "Fallback identificado como orçamento provisório; origem e janela manual acessíveis nos detalhes do modelo.",
        "model-fallback",
    ),
    (
        "F03",
        "Resize recalcula a variante compacta; plano/pergunta rolam internamente e preservam chat, input e rodapé.",
        "ask-choice",
    ),
    (
        "F04",
        "Rodapé contextual, pergunta pausada identificada e botão Enviar desabilitado quando não há resposta/opção.",
        "ask-text",
    ),
    (
        "F05",
        "Comandos frequentes primeiro, navegação por setas/Enter/Tab/Esc e estado vazio persistente no menu.",
        "slash-empty",
    ),
    (
        "F06",
        "Campos compactos, URL visível para endpoints locais/compatíveis, autenticação opcional do Ollama e aviso TLS junto à opção.",
        "provider-custom",
    ),
    (
        "F07",
        "Teste de catálogo separado de inferência; teste completo também no seletor, sem ativar o modelo nem salvar o perfil.",
        "model-tools",
    ),
    (
        "F08",
        "Lista tem carregamento e vazio próprios; modelos incompatíveis permanecem visíveis e desabilitados.",
        "models-empty",
    ),
    (
        "F09",
        "Ajuda/status são modais com campos legíveis e diagnóstico em Avançado, sem adicionar mensagens ao chat.",
        "status",
    ),
    (
        "F10",
        "Revisão do plano no chat, Ajustar/Executar com revisão e modo preparado para o próximo pedido sem alterar a execução atual.",
        "plan",
    ),
    (
        "F11",
        "Diff com cores do tema e contagem por arquivo; erros com detalhes recolhidos e ações próprias; recibo baseado nas validações da revisão atual.",
        "done",
    ),
    (
        "F12",
        "Primeiro acesso limpo, controle de resumos, oferta de retomada e nova conversa curta, preservando a sessão anterior.",
        "first-run",
    ),
    (
        "F13",
        "Falha de capacidade oferece escopo no input, com uma nova tentativa delimitada; busca vazia oferece ações; compactação preserva o histórico completo salvo.",
        "context-scope",
    ),
    (
        "F14",
        "Menos títulos de raciocínio, contexto usado/orçamento compacto, recuos e cores consistentes, política de edições junto ao input.",
        "stages-expanded",
    ),
]


def main():
    catalog = json.loads((PROTO / "screens.json").read_text())["screens"]
    checks = json.loads((OUT / "interactions.json").read_text()) + json.loads(
        (OUT / "extra-interactions.json").read_text()
    )
    observations = json.loads((OUT / "observations.json").read_text())
    assert not [row for row in observations if row.get("error")]
    assert all(row["passed"] for row in checks)
    screenshots = list((OUT / "screens").glob("*.svg"))
    assert len(screenshots) == 100
    for path in screenshots:
        ET.parse(path)
    by_group = {
        "Início": "F12",
        "Entrada": "F04, F05",
        "Conversa": "F11, F14",
        "Perguntas": "F01, F03, F04",
        "Execução": "F10, F11",
        "Provedores": "F06, F07",
        "Modelos": "F02, F07, F08",
        "Configuração": "F09, F10, F12",
        "Recuperação": "F01, F11, F13",
    }
    rows = []
    for row in catalog:
        details = [description for _, description, scene in CORRECTIONS if scene == row["id"]]
        rows.append(
            {
                **row,
                "assessment": "Revisado",
                "priority": by_group[row["group"]],
                "detail": " ".join(details)
                or "Estado revisitado na implementação corrigida. Consulte a captura e os checks de interação.",
            }
        )
        for size in ("120x35", "80x24"):
            assert (OUT / "screens" / f"{row['id']}-{size}.svg").exists()
    (OUT / "matrix.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    gallery = (BEFORE / "gallery.html").read_text()
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    gallery = re.sub(
        r"const rows=.*?;const scene=",
        lambda _: "const rows=" + data + ";const scene=",
        gallery,
        flags=re.S,
    )
    gallery = gallery.replace(
        "protótipo aprovado × implementação", "protótipo aprovado × implementação corrigida"
    )
    gallery = gallery.replace("Implementação · captura nativa", "Implementação · após correções")
    gallery = gallery.replace(
        "Estados ausentes mostram a tela equivalente mais próxima.",
        "Os dados das capturas são sintéticos; nenhum provedor real foi consultado.",
    )
    gallery = gallery.replace(
        '<a href="README.md">',
        '<a href="../2026-10-21-all-screens-comparison/gallery.html">Ver comparação anterior</a><a href="README.md">',
    )
    (OUT / "gallery.html").write_text(gallery)
    script = re.search(r"<script>(.*?)</script>", gallery, re.S).group(1)
    with tempfile.NamedTemporaryFile(suffix=".js") as file:
        file.write(script.encode())
        file.flush()
        subprocess.run(["node", "--check", file.name], check=True)
    report = [
        "# Correções da comparação completa de UI/UX",
        "\nOs 14 achados da comparação foram tratados. A galeria apresenta os 46 estados na versão corrigida. A comparação anterior foi preservada; não alteramos o desenho do protótipo para fazer a implementação parecer aderente.",
        "\n[Galeria das 46 telas após as correções](gallery.html) · [Auditoria anterior](../2026-10-21-all-screens-comparison/README.md) · [Matriz JSON](matrix.json)",
        "\n## Verificação",
        f"\n- **{len(checks)}/{len(checks)} checks específicos passaram**, incluindo as cinco expectativas antes não atendidas.\n- **100 capturas nativas**: 46 estados em 120×35 e 80×24, duas perguntas pausadas e seis capturas complementares.\n- JSON, XML e referências das 46 telas verificados; JavaScript da galeria validado.\n- A suíte completa, lint e empacotamento estão registrados em [validation.txt](validation.txt).",
        "\nO renderizador é o Textual real em modo headless. Eventos, resultados e HTTP são sintéticos, sem API key real, inferência remota ou custo. A tela de diagnóstico de ferramentas percorre um protocolo simulado de chamada → resultado → resposta final. O recibo de conclusão usa um registro de validação sintético para demonstrar o componente; essa captura não representa testes executados num repositório real. A pergunta de escopo da galeria ilustra o componente; o gatilho por falha de capacidade é testado em test_ui_parity_fixes.py.",
        "\nIsso verifica os fluxos e a composição nativa, sem prometer identidade de pixels entre HTML e terminal. Fontes, leitores de tela e entrega de teclas Command em terminais físicos precisam de conferência nesses ambientes.",
        "\n## Correções entregues",
        "\n| Achado | Correção | Captura |\n| --- | --- | --- |",
    ]
    for identifier, description, scene in CORRECTIONS:
        report.append(f"| {identifier} | {description} | [Ver](screens/{scene}-120x35.svg) |")
    report += [
        "\nA revisão do plano fica na conversa, mantendo o acompanhamento fixo junto ao input. Executar com revisão inicia a tarefa e continua respeitando as permissões; Ajustar seleciona Planejar. Uma seleção de modo durante geração é aplicada somente ao próximo pedido; uma ação explícita posterior de executar/ajustar o plano prevalece sobre essa seleção pendente.",
        "\nA nova conversa recebe uma sessão independente. A anterior continua disponível em /sessions e /session ID. Cancelar a confirmação preserva o rascunho. O formato anterior de session.json continua aceito; decisões e decisões pendentes usam metadados locais limitados e redigidos com a mesma proteção de credenciais do restante da sessão.",
        "\nA recuperação de contexto pode pedir escopo após esgotar a recuperação do agente. Uma resposta permite uma tentativa com escopo delimitado. Se instruções/ferramentas ainda não couberem, ficam disponíveis configuração e troca de modelo, evitando repetição infinita de perguntas. Não é possível prometer que qualquer tarefa caberá em qualquer modelo.",
        "\n## Todas as telas",
        "\n| Estado | Captura normal | Compacta | Referência |\n| --- | --- | --- | --- |",
    ]
    for row in rows:
        scene = row["id"]
        report.append(
            f"| {row['title']} | [120×35](screens/{scene}-120x35.svg) | [80×24](screens/{scene}-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/{scene}.svg) |"
        )
    report += [
        "\n## Reproduzir",
        "\n```bash\n.venv/bin/python docs/audits/2026-10-21-ui-parity-fixes/reproduce.py\n.venv/bin/python docs/audits/2026-10-21-ui-parity-fixes/interactions_extra.py\n.venv/bin/python docs/audits/2026-10-21-ui-parity-fixes/build_report.py\n.venv/bin/pytest -q\n```",
    ]
    (OUT / "README.md").write_text("\n".join(report) + "\n")
    spec = importlib.util.spec_from_file_location("prior_comparison", BEFORE / "build_report.py")
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    prior.OUT = OUT
    with tempfile.TemporaryFile() as log:
        for scene in ["provider", "models", "ask-choice", "status", "plan"]:
            destination = OUT / f"comparison-{scene}.svg"
            prior.comparison(scene, destination)
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
    print(
        f"46 scenes; {len(screenshots)} native captures; {len(checks)}/{len(checks)} checks passed"
    )


if __name__ == "__main__":
    main()
