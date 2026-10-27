# Correções e validação da auditoria de UI/UX

Data: 27/10/2026. Os achados originais e as evidências anteriores estão em
[report.md](report.md). Esta entrega implementa os dez itens, mantendo o início
limpo, a conversa sem painel lateral e o componente único de pergunta/resposta.

| Item | Resultado |
| --- | --- |
| UI-01 | Cancelamento sinaliza os jobs próprios da conversa; processos novos verificam cancelamento antes de iniciar. A linha de comandos ativos e `/jobs` mostram saída paginada, estado e ação de parar. A UI confirma término real. |
| UI-02 | Edição e catálogo salvo usam metadados sem recuperar a chave. Testar/salvar resolve a credencial sob demanda, fora da thread da interface; falha é recuperável sem alterar o perfil ou o rascunho. |
| UI-03 | HTTP, HTTPS com verificação habilitada e HTTPS sem verificação têm rótulos distintos; política não é apresentada como teste já realizado. |
| UI-04 | Pergunta em terminal baixo usa input de uma linha e resumo do plano, mantendo a preferência de expansão e permitindo expandir manualmente. |
| UI-05 | Opções preservam começo e fim; a seleção pode ocupar duas linhas em terminais maiores. Valores completos são enviados, sem precisar reabrir um modal para distinguir finais diferentes. |
| UI-06 | Escopo tem resumo, corpo rolável, campos compactos, ações fixas e detalhes. O resumo corresponde também ao JSON avançado e sinaliza edição inválida. |
| UI-07 | Placeholder usa cor semântica sem dim; contraste mínimo de 4,5:1 verificado nas cores exportadas nos dois temas. |
| UI-08 | Ajuda em Avançado distingue arquivo privado e cofre; informa dependência ausente sem tentar desbloquear ao renderizar. |
| UI-09 | Gerenciador e aprovação de comando ajustam altura ao conteúdo, com ações fixas e foco inicial seguro. |
| UI-10 | Parágrafos, listas e código têm espaçamento menor nos terminais baixos; composição maior preserva respiro e largura de leitura. |

## Verificação executada

- Suíte completa local: **890 aprovados, 18 pulados**. A execução usa Go SDK local
  e Node para as fixtures; testes reais de provedores continuam opt-in.
- **17 regressões específicas aprovadas** após os ajustes finais de apresentação,
  além das verificações de aprovação com objetivo/comando longos.
- Recuperação de código e distribuição são verificadas com os comandos existentes
  de avaliação, build e smoke do pacote instalado, antes do push.
- A reprodução adicional confirma `cancelled` após cancelar a UI/plano nos três
  tamanhos e formulário aberto sem exceção quando o cofre está bloqueado.
- As capturas principais agora cobrem **33 estados em quatro tamanhos** (132 SVGs
  e textos). Incluem a linha de comandos ativos e a tela de saída. As capturas
  adicionais exercitam cofre, HTTP, tema claro, escopo e erros recuperáveis.
- Evidências posteriores: `artifacts/ui-fixes-2026-10-27/`; capturas e comparações
  anteriores permanecem em `artifacts/ui-audit-2026-10-27/` e no ZIP da auditoria.

Os testes de cancelamento usam um processo Python local com descendente e
verificam que o arquivo de heartbeat para de mudar antes de fechar a aplicação.
Outro caso cancela durante aprovação; outro cancela durante pergunta sem registrar
uma decisão presumida. A consulta de saída não chama o modelo. Dados dos provedores
são sintéticos e não expõem credenciais reais.

As capturas são da aplicação Textual headless, sem inferência. Medidas de contraste
não certificam fontes, terminais ou tecnologias assistivas reais. Comportamentos
nativos de arquivo/processo continuam cobertos pela matriz de distribuição do CI.

## Reproduzir as capturas adicionais em outra pasta

```bash
CODARO_UI_AUDIT_OUTPUT="$PWD/artifacts/ui-fixes-2026-10-27" python docs/audits/2026-10-27-ui-ux/reproduce.py
CODARO_UI_AUDIT_OUTPUT="$PWD/artifacts/ui-fixes-2026-10-27" python docs/audits/2026-10-27-ui-ux/capture_extra_states.py
```

Use um terminal com cores habilitadas, ou remova `NO_COLOR` apenas para esses
comandos. Os scripts mantêm perfis e projetos em diretórios temporários.

Resultado final local: **19/19** casos de recuperação, wheel/sdist aprovados em
`twine check`, smoke instalado fora do checkout aprovado e três verificações de
portabilidade aprovadas no Linux. A reprodução confirmou término dos jobs e
rótulo HTTP correto em 60×20, 80×24 e 120×35, formulário recuperável com cofre
bloqueado e geometria estável nos 33 controles verificados por hover.
