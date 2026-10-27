# Confiabilidade, recuperação e qualificação

Implementação de 27/10/2026. Os recursos abaixo complementam o fluxo existente;
qualificação de modelos e validação em máquinas externas exigem execução própria.

## 1. Qualificação por modelo/provedor

O runner usa projetos sintéticos e não muda o provedor selecionado no aplicativo:

```bash
python scripts/qualify_models.py --profile Local --allow-model-calls --allow-execution --output artifacts/qualification-local.json
```

`--allow-model-calls` autoriza inferência no perfil informado. `--allow-execution`
autoriza os testes dos cinco projetos descartáveis. Sem essa última opção, a
bateria de implementação é ignorada e o modelo não recebe qualificação completa.
Também é possível analisar um JUnit existente sem realizar novas chamadas:

```bash
python scripts/qualify_models.py --junit artifacts/live.xml --output artifacts/qualification.json
```

O relatório exige catálogo, streaming, protocolo nativo de ferramentas, análise
com leitura de fontes, recuperação histórica/decisões após descarte e reinício,
leitura paginada e implementação validada nas cinco fixtures. Guarda modelo,
protocolo, resultados, durações e métricas numéricas disponíveis. Qualquer teste
necessário ausente, ignorado ou falho impede a qualificação. Custo desconhecido
continua `null`; não se atribui consumo zero quando o provedor não o informou.

O workflow de release agora exige `suite=full` e `implementation=true` e publica
o relatório de qualificação. `CODARO_QUALIFICATION_MODEL` e
`CODARO_QUALIFICATION_API_STYLE` configuram a gate. O padrão é Ollama local/Qwen
2.5 3B, sem API paga. Os problemas conhecidos desse modelo pequeno continuam
válidos: não se presume que ele passe na bateria completa. Para um endpoint
remoto, configure explicitamente o environment `live-providers`, URL e chave.

## 2. Índice por lotes de alterações

A UI inicia um observador nativo com `watchfiles`. Eventos são agrupados por
150 ms e aplicados ao índice derivado. Arquivos novos/removidos e mudanças de
ignore continuam sendo reconciliados com a listagem permitida do repositório.
Uma reconciliação completa ocorre periodicamente, a cada cinco minutos, para
reduzir dependência de eventos que o sistema operacional possa perder.

As consultas mantêm suas verificações de atualidade e política; o observador
não é um cache de autorização. Se ele falhar, a atualização normal nas consultas
continua disponível. Não inicia comandos do projeto nem escreve nos fontes.
O fechamento sinaliza o observador e coleta sua thread. A listagem usada pelo
índice e pela visão geral também recebe o cancelamento da investigação.

## 3. Contexto após descarte e retomada

A aceitação opt-in inclui três decisões distintas, doze rodadas de descarte de
histórico e uma nova instância do agente. A resposta deve recuperar os valores
confirmados, preservando a política de autorização. Isso exercita retenção
estrutural e recuperação real; não comprova a qualidade de qualquer resumo
semântico em todas as situações. As regressões existentes de compactação,
protocolo e restrições continuam fazendo parte da suíte padrão.

## 4. Reconciliação de operações interrompidas

`/recovery` consulta os registros duráveis já existentes, sem executar ferramentas
do modelo. Para checkpoints em estado `ready`, compara existência e hashes dos
arquivos com os snapshots antes/depois e informa:

- conteúdo anterior preservado;
- conteúdo correspondente ao proposto, com validação pendente;
- conflito com os dois snapshots;
- caminho indisponível ou bloqueado pela política.

Também aponta comandos com intenção persistida e sem recibo de término. O estado
desses comandos é desconhecido; eles nunca são repetidos automaticamente.
A abertura da UI verifica pendências em segundo plano e mostra um aviso curto.
`/changes` permite revisar as alterações e checkpoints antes de outra atividade.

A correspondência do conteúdo não comprova que um comando terminou nem que testes
passaram. A detecção depende dos registros retidos: checkpoints e eventos possuem
cotas próprias. Uma queda não é uma transação do filesystem, e efeitos externos
continuam exigindo revisão. Não se concedem permissões ao recuperar registros.

## 5. Fases e responsabilidades

`agent/progress.py` valida fases tipadas, início em preparação e impossibilidade
de reiniciar uma execução terminal. Esperas por aprovação/perguntas são explícitas
e restauram a fase anterior mesmo após uma exceção. Os contratos permanecem
separados da confirmação de alterações e da validação por recibos reais.

O controlador e a UI continuam fachadas públicas. Essa é uma decomposição
incremental; o loop ainda é um módulo grande, e as fases não são prova de que uma
solicitação arbitrária foi semanticamente cumprida.

## 6. Comparação de recuperação e embeddings

```bash
python scripts/benchmark_retrieval.py --output artifacts/retrieval-lexical.json
python scripts/benchmark_retrieval.py --model BAAI/bge-small-en-v1.5 --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 --allow-downloads --output artifacts/retrieval-comparison.json
```

O segundo comando exige `codaro[semantic]` e pode baixar pesos. Modelos não
suportados pelo fastembed instalado são rejeitados antes de iniciar a comparação.
Não há inferência de LLM. O relatório registra recall, posição do primeiro
resultado relevante, tokens estimados, duração e estado do backend semântico,
inclusive aquecimento parcial ou indisponibilidade. Compare o mesmo corpus e a
mesma máquina; pesos/modelos diferentes não são automaticamente melhores.

Na comparação local dos 13 casos fixos da fixture poliglota, busca lexical,
BGE em inglês e MiniLM multilíngue acertaram 13/13, com recall e MRR iguais a 1.
Os tokens estimados retornados foram 2.773, 4.662 e 4.832, respectivamente.
O teste não demonstrou ganho de acerto com embeddings nesse corpus. A primeira
execução multilíngue incluiu download/carregamento: seus tempos não são uma
comparação de latência em regime. Casos maiores no repositório em edição foram
interrompidos e não são usados como evidência comparativa.

O encoder usa duas threads por padrão para evitar um pool ONNX proporcional ao
host inteiro. `CODARO_EMBEDDING_THREADS` permite 1–16. A busca lexical continua
como fallback. O modelo de embedding padrão não foi trocado sem evidência.

## 7. Candidato de distribuição

Wheel e sdist são construídos e verificados com `twine check`; o smoke instalado
fora do checkout e os testes de portabilidade complementam os testes de fontes.
O CI conserva instalação nativa Linux/macOS/Windows. Resultados locais Linux não
comprovam execução nos demais sistemas nem substituem uma rodada com usuários
externos. Nenhuma tag, publicação PyPI ou release pública é criada por esses testes.

## Processo visível e transporte

Pensamento textual enviado pelo modelo, ferramentas em andamento e resultados
ficam em ordem durante a execução. A fila monta também eventos que chegam antes
do primeiro refresh. Ao concluir, uma linha recolhida **Ver processo** mantém as
etapas acessíveis, com a resposta final como componente separado da conversa.
Prévias longas continuam limitadas e identificam omissão; não se promete arquivo
integral além das cotas do diagnóstico. A escolha manual de recolher é preservada
até a conclusão. A rolagem automática é agrupada por refresh e deixa de avançar
se o usuário voltar para ler mensagens anteriores.

O modelo selecionado aparece somente no seletor do cabeçalho. A confirmação de
seleção não repete o nome no rodapé, que volta a mostrar o estado da tarefa.

O transporte SSE/NDJSON aceita **16 MiB** por chamada, configuráveis com
`CODARO_MAX_STREAM_MB` entre 1 e 128. Esse teto conta tráfego, não tokens de
contexto. Eventos individuais têm teto de 1 MiB; argumentos e texto mantêm seus
limites independentes. O parser evita varrer novamente uma linha inteira a cada
fragmento pequeno, preservando validação UTF-8 e cancelamento.

## Evidências desta rodada

- Suíte completa: 917 aprovados, 19 ignorados (inclui aceitação opt-in).
- HTTP com servidor local e IPC habilitado: 21 aprovados.
- Recuperação lexical: 19/19 casos; comparações de embeddings em corpus sintético
  poliglota ficam em `artifacts/maturity-followup/` com o estado do backend.
- Capturas da UI real com dados sintéticos: 144 estados/tamanhos gerais e 20
  capturas específicas do processo. Use `scripts/capture_ui.py` e
  `scripts/capture_process.py` para reproduzir.
- O benchmark de histórico com 30 respostas manteve 130 componentes, cerca de
  1,8 s para abrir e 101 ms para sair no teste headless desta rodada.
- Wheel/sdist aprovados no `twine check`; smoke do pacote instalado fora do
  checkout aprovado e três testes nativos de portabilidade aprovados no Linux.
- Após os últimos ajustes do acompanhamento de leitura, 96 testes de UI,
  navegação, processamento e performance passaram novamente.

Essas verificações não qualificam o servidor/modelo do usuário nem comprovam
qualidade em todo projeto real. As instruções acima tornam essa aceitação
concreta e reproduzível sem apresentar testes simulados como inferência real.
