# Auditoria de maturidade do Codaro — 7 de outubro de 2026

Revisão do commit `7955e55dc938716e40b79656fa2ce2a3f22eb0dc`, branch `develop`. Escopo: CLI/TUI, agente, ferramentas, permissões, arquivos/edições, checkpoints, tarefas/memória, provedores BYOK, protocolos HTTP/SSE, contexto, recuperação, busca, testes, documentação e entrega. Os achados desta revisão estão pendentes de correção; os arquivos deste diretório são documentação e reproduções isoladas.

## Conclusão

O produto tem uma base funcional consistente: 433 testes passaram, Ruff e formatação passaram e os três casos locais de recuperação passaram. Há controles efetivos de autorização humana, observação de trechos antes de editar, conflitos, checkpoints e ferramentas nativas. Antes de ampliar autonomia, devem ser corrigidos três problemas reproduzidos: proteção do índice contra hard links, preservação de redação de credenciais após troca de provedor e vínculo entre validação e revisão do código. O isolamento de subprocessos é uma prioridade adicional de arquitetura.

Esta auditoria distingue defeitos reproduzidos, comportamentos de arquitetura reproduzidos e riscos identificados por inspeção. Não constitui certificação de segurança nem avaliação semântica dos seis provedores com credenciais reais. Não foram feitas chamadas pagas, testes em serviços externos, varredura atual de CVEs ou medição de cobertura de código.

## Evidências e método

- Suíte: `433 passed in 63.68s`; Ruff, formatação e `git diff --check` aprovados.
- Recuperação local: `codaro evaluate evaluations/cases.json --output /tmp/codaro-audit-20261007/retrieval.json`, resultado `3/3`.
- Seis reproduções determinísticas, com projetos temporários, chaves fictícias e sem rede: [reproduce.py](reproduce.py) e [reproductions.json](reproductions.json).
- Resultado detalhado da busca: [retrieval.json](retrieval.json). Seus critérios de relevância usam somente os caminhos esperados nos três casos de fumaça.
- Revisão de mecanismos e testes existentes; não foram modificados código de produção ou testes nesta etapa.

Para repetir as reproduções em Linux/macOS, a partir da raiz do projeto:

```bash
.venv/bin/python docs/audits/2026-10-07/reproduce.py
```

O script cria e remove seus próprios arquivos em diretórios temporários. A reprodução de hard link depende de POSIX e de suporte a hard links no filesystem temporário.

## Achados prioritários

P1: corrigir antes de aumentar autonomia ou distribuir amplamente. P2: confiabilidade, compatibilidade e operação. P3: manutenção e acabamento. A prioridade considera o uso como agente local de desenvolvimento; não pressupõe acesso remoto não autorizado.

### A01 — P1 — Índice SQLite aceita hard link para banco externo

**Defeito reproduzido.** Em `src/codaro/index.py:17–35`, os arquivos do índice são verificados por `is_symlink`, mas o banco é aberto diretamente por `sqlite3.connect` e recebe `chmod`. Não há a verificação de `st_nlink` aplicada aos outros armazenamentos privados.

A reprodução ligou `.codaro/index.sqlite3` a um SQLite fictício fora do projeto. A inicialização do índice apagou o registro `outside.py` da tabela `files` externa durante a migração e mudou suas permissões para `0600`. Exige um hard link já presente no filesystem local; não é um exploit remoto demonstrado.

**Recomendação:** verificar tipo regular/número de links e proteger também journals/WAL/SHM; fixar o diretório e impedir trocas entre validação e abertura. Considerar abrir snapshots verificados ou armazenar o índice em cache privado independente do projeto. Adicionar regressões para hard links, substituição concorrente, versões incompatíveis e corrupção.

**Aceite:** abrir/indexar um projeto com esses arquivos rejeita o caminho e conserva conteúdo, schema e modo do arquivo externo.

### A02 — P1 — Trocar de provedor pode reintroduzir uma chave antiga no histórico/debug

**Defeito reproduzido, condicionado à presença da chave no conteúdo.** `CodaroApp.activate_provider`, em `src/codaro/tui.py:926`, substitui `session.secret`. `SessionStore` e `PromptFlow` mascaram somente a chave configurada para aquele objeto/fluxo. A memória do agente acumula redatores antigos, mas essa proteção não abrange uniformemente sessão, trace e histórico enviado ao novo modelo.

Uma chave fictícia apareceu mascarada na primeira gravação da sessão e voltou a aparecer em texto após trocar `session.secret` e salvar os mesmos turnos mantidos em memória. Um novo `PromptFlow` com a chave atual também gravou a antiga presente no histórico. O formulário BYOK não coloca a chave na conversa; o problema ocorre caso uma chave já tenha aparecido em mensagens, código consultado ou saídas.

**Recomendação:** um serviço único de redação com todas as credenciais conhecidas pela sessão, usado na persistência, debug, erros e conteúdo elegível para envio. Sanitizar também registros em memória quando necessário. Separar segredos do perfil mediante keyring do sistema; oferecer referência a variável de ambiente e mecanismo de rotação. Evitar fabricar criptografia cujo segredo fique ao lado do arquivo.

**Aceite:** cadastrar/trocar A → B → C mantém todas as chaves conhecidas mascaradas nas exportações/sessão/trace, incluindo variantes escapadas; mensagens e saídas fictícias cobrem a regressão.

### A03 — P1 — Validação que muda arquivos é atribuída à revisão posterior

**Defeito de garantia reproduzido.** Em `src/codaro/agent.py:2022–2028`, o comando executa, `sync_workspace` atualiza a revisão e só então `tasks.validation` registra a revisão corrente.

Um comando declarado como validação apenas alterou `x.py` de `x = 1` para `x = 2` e terminou com código zero. A revisão passou de 0 para 1, o resultado foi atribuído à revisão 1, `validation_ready()` retornou `True` e `finish_task` aceitou `completed`. Não havia uma verificação do código novo na reprodução. Isso é distinto da limitação já documentada de que exit code zero não prova cobertura semântica.

**Recomendação:** capturar revisão/digest no início e no fim da verificação; se fontes mudarem durante o comando, exigir nova validação do estado final, salvo uma política explícita para artefatos gerados. Registrar comando, diretório, identidade da revisão, início/fim e resultado. Manter a garantia de que todas as verificações requeridas passaram no mesmo estado final.

**Aceite:** testes que alteram fontes e terminam com zero deixam validação pendente; uma segunda execução sobre fontes estáveis libera conclusão. Mudanças somente em artefatos explicitamente excluídos não invalidam verificações sem motivo.

### A04 — P1 — Aprovação de comando não delimita filesystem, rede ou ambiente

**Comportamento de arquitetura reproduzido; aprovação humana continua funcionando.** `src/codaro/commands.py:35–80` usa `cwd`, `shell=False` e encerra o grupo de processos em POSIX. O subprocesso conserva as permissões do usuário e herda o ambiente, removendo somente `CODARO_API_KEY`.

Um comando aprovado escreveu um marcador fora do projeto e recebeu uma chave fictícia em outra variável. O diretório de trabalho e a autorização de caminhos para ferramentas de edição não restringem o que um script de teste, build ou instalador faz. Comandos previamente aprovados podem executar código que mudou desde a autorização.

**Recomendação:** executor com sandbox e diretórios/network explicitamente autorizados, ambiente por allowlist e separação de comandos locais/rede. Oferecer execução isolada em worktree/container quando aplicável. Mostrar o alcance efetivo na aprovação e vincular permissões duradouras a tarefa/revisão/política. Rejeitar automaticamente somente o que não foi autorizado pela política vigente.

**Aceite:** no modo isolado, o marcador externo não é criado, chaves não autorizadas não chegam ao filho e rede fora do escopo é bloqueada. Manter opção explícita de execução com permissões do usuário quando necessária.

## Achados de confiabilidade e operação

### A05 — P2 — Calibração Anthropic usa payload diferente do contado

**Defeito reproduzido.** O orçamento usa `wire(payload)`, enquanto `src/codaro/agent.py:1127` chama `counter.observe(payload, actual)` com o formato normalizado. Na reprodução, as bases foram 1.581 e 1.485, e a amostra resultou em `0,939278...`, quando deveria ser 1 ao calibrar a estimativa do próprio payload nativo. Não há medição real de tokens Anthropic nessa reprodução; ela demonstra a inconsistência entre as duas bases.

**Recomendação:** calibrar sempre o mesmo payload efetivamente enviado e incluir versão do protocolo/contador na identidade da calibração. Testar system separado, tool use/result e cache. Exibir estimativa, valor informado e margem separadamente.

### A06 — P2 — Busca quente relê todos os arquivos

**Comportamento reproduzido.** `CodeIndex.search` chama `update`; `update` calcula hash a partir da leitura integral de cada arquivo permitido. Um projeto de 200 arquivos inalterados gerou 200 leituras numa única busca quente. A atualização incremental economiza parsing, mas ainda relê as fontes; o agente também chama atualização para snapshots da tarefa.

**Recomendação:** compartilhar snapshot por interação, usar metadados/watchers para localizar mudanças e reservar hashing completo para sincronização ou confirmação de segurança. Sempre conferir bytes atuais antes de ler para edição/aplicar. Impedir que cache de performance enfraqueça a proteção contra conflitos e mudanças de ignore.

**Aceite:** em benchmarks de 1 mil/10 mil/20 mil arquivos, publicar latência p50/p95, leituras e bytes por busca quente; manter regressões de mudança externa, ignore e conflito.

### A07 — P2 — Prazo global e cancelamento HTTP têm aplicação tardia

**Inspeção confirmada, sem simulação de servidor remoto lento.** O deadline é verificado entre operações em `Agent._ask`; HTTP usa timeout por operação/inatividade e cancelamento entre fragmentos. Um pedido em andamento pode exceder o prazo global; SSE com dados periódicos pode prolongar a chamada. A espera de aprovação humana é corretamente excluída do prazo atual.

**Recomendação:** propagar tempo restante/cancelamento até o transporte, índice e executor; usar encerramento cooperativo da conexão e deadline absoluto, mantendo a exclusão do tempo de aprovação. Registrar motivo de parada e distinguir cancelamento, timeout de rede e limite global.

**Aceite:** testar servidor local que não envia dados, que envia heartbeats e que entrega resposta parcial; medir tempo até cancelar e confirmar que não ocorrem ferramentas após expirar o prazo.

### A08 — P2 — Debug tem custo de gravação e tamanho agregado sem orçamento próprio

**Inspeção.** `PromptFlow.checkpoint` reserializa/regrava o fluxo inteiro em cada checkpoint, com cópias de requests, respostas, resultados e eventos SSE. Há limites por chamada e por etapas, mas não um teto específico do trace agregado. As configurações permitidas comportam dezenas ou centenas de MB em casos extremos; isso não foi observado como consumo real nesta auditoria.

**Recomendação:** manter o último fluxo sempre recuperável, conforme requisito do produto, e usar journal/eventos incrementais mais snapshot compatível. Definir orçamento/compactação de debug, preservar os corpos completos em arquivos referenciados quando necessário, indicar truncamento e origem. A UI deve avisar quando a gravação falhar; retenção de fluxos antigos deve ser opcional e limitada.

**Aceite:** injetar falha de disco/permite leitura, trace grande e encerramento abrupto; o último JSON permanece válido ou a falha aparece ao usuário, com referências completas recuperáveis e credenciais mascaradas.

### A09 — P2 — Cadastro de modelo precisa negociar capacidades de inferência

**Inspeção e limite dos testes.** O catálogo rejeita `tools=False`, mas aceita capacidade desconhecida. A listagem de modelos pode incluir outras modalidades. `build_payload` fixa `temperature` e `max_tokens`; não há política por modelo para parâmetros opcionais, endpoints ou limites ativos. O diagnóstico de ferramenta já existe e pode ser reaproveitado.

**Recomendação:** descrever capacidades como conhecido/suportado/desconhecido; filtrar modelos elegíveis para chat e oferecer teste explícito de ferramenta completo ao selecionar. Adaptar parâmetros com contratos por provedor/modelo, inclusive limites realmente ativos no servidor Ollama. Guardar resultado do diagnóstico com endpoint/modelo/versão e validade. Qualquer teste de inferência pago deve ser claramente apresentado ao usuário.

**Aceite:** fixtures por provedor para catálogo, erro, SSE, parâmetros opcionais e tool result; testes reais opcionais com credenciais externas e orçamento de custo. Não rotular um modelo como validado apenas porque apareceu no catálogo.

### A10 — P2 — Retomada e undo devem operar por conjunto de alterações

**Inspeção; comportamento parcial já é identificado no código.** Alterações são aplicadas por arquivo, podem resultar em conjunto parcial e checkpoints retêm os últimos 20 arquivos. Existe intenção persistida antes da escrita, mas recuperação exige interpretação de estados e undo individual. Não foi demonstrada perda de dados num crash nesta auditoria.

**Recomendação:** changeset com ID, operação/revisão/paths/checkpoints e estados explícitos; reconciliação ao abrir após crash; undo do conjunto em ordem reversa com conflitos verificados. Oferecer worktree para execução e revisão. Incluir `doctor` com verificação/backup/rebuild para índice e quarentena/reparo dos armazenamentos de sessão/memória/tarefas; corrupção da memória obrigatória pode impedir novas perguntas.

**Aceite:** fault injection antes/depois de cada escrita e atualização do journal, múltiplas instâncias e disco cheio; retomada descreve exatamente o que ocorreu e nunca reaplica operações por suposição.

### A11 — P2 — Janela do modelo e orçamento do cliente precisam aparecer juntos

**Inspeção; limites conservadores são uma escolha existente.** O agente tem teto adicional padrão de 64.000 caracteres de payload, histórico de 16.000 caracteres e saída padrão de 1.400 tokens. Selecionar um modelo com janela maior não remove esses tetos. A UI pode mostrar a janela da API sem tornar evidente o limite que efetivamente causou compactação. A reserva pequena de saída também limita mudanças grandes.

**Recomendação:** configuração por perfil/modo, mostrando janela reportada, teto local, orçamento disponível, saída reservada e motivo da compactação. Escalonar saída para tarefas de implementação dentro dos limites informados. Manter fallback explícito quando a API omitir contexto; não inferir janela ativa de capacidade arquitetural.

**Aceite:** modelos com janelas distintas produzem orçamento coerente e explicável; métricas mostram quem limitou a chamada, sem depender de inspeção manual do JSON.

## Maturidade do produto

### A12 — P2 — Testes de qualidade precisam cobrir projetos reais e várias linguagens

**Inspeção.** Os três casos de avaliação usam o próprio Codaro e um demo. O AST é específico de Python; outras linguagens recebem janelas textuais. Os 433 testes verificam mecanismos e muitas regressões, mas não demonstram sucesso autônomo em atividades reais ou qualidade semântica com todos os modelos.

**Recomendação:** corpus versionado de projetos pequenos/médios/monorepos em Python, TypeScript/JavaScript, Go e Rust; tarefas de pergunta, planejamento, edição, múltiplos arquivos, recuperação e testes. Usar ground truth revisado, incluir consultas em português/inglês e casos sem resposta suficiente. Estender AST/LSP conforme demanda; avaliar embeddings/reranking contra uma baseline antes de adotá-los.

**Métricas:** sucesso de tarefa confirmado por testes, precisão de recuperação, tokens por tarefa resolvida, número de leituras, compactações, chamadas repetidas, falhas de protocolo, falsos estados concluídos e latência. No resultado atual, a precisão por caminhos esperados do primeiro caso foi 0,33, com recall 1; esse rótulo restrito não prova que os demais caminhos eram irrelevantes.

### A13 — P2 — Entrega, suporte e segurança das dependências

**Inspeção.** CI cobre Python 3.11–3.13 em Ubuntu. Não há matriz macOS/Windows, requisito de cobertura, verificação de wheel instalado em ambiente limpo ou análise de dependências no workflow. O projeto segue na versão 0.1.0; não foram encontrados LICENSE, SECURITY.md, changelog ou política de atualização nas buscas realizadas.

**Recomendação:** testar Linux/macOS e documentar suporte parcial de Windows, especialmente edição segura, locks e encerramento de filhos. Publicar wheel/sdist testados em ambientes limpos, releases versionadas e changelog; adicionar licença escolhida pelo mantenedor e canal de segurança. Incluir auditoria de dependências/SBOM, atualizações automatizadas, integridade do lock e cobertura dos caminhos críticos. Ausência dessas verificações não significa que uma dependência vulnerável foi comprovada.

**Aceite:** instalar artefato de release e executar CLI/diagnóstico smoke sem checkout; suporte por plataforma claro; dependências e incidentes com fluxo definido.

### A14 — P3 — Separação de responsabilidades e UX de operação

**Inspeção.** `agent.py` tem 2.076 linhas e `tui.py` 1.480. Transporte, orçamento, execução e apresentação usam várias estruturas dict/callbacks. Definições de ferramentas e validações dependem de disciplina manual. Na UI, cadastro/seleção já estão no menu, mas edição/rotação/exclusão de perfis continua incompleta como experiência integrada.

**Recomendação:** extrair orquestração de turnos, registro/validação de ferramentas, política/executor e orçamento para componentes com contratos tipados; evitar reescrever tudo simultaneamente. Centralizar schemas, erros e redação. Evoluir o menu para gerir perfis, testar conexão, trocar chave, restaurar configuração e exportar diagnóstico seguro. Estado final deve distinguir resposta entregue, tarefa concluída, validação pendente, conjunto parcial e cancelamento.

## Roteiro recomendado

| Etapa | Entregas | Critério para avançar |
|---|---|---|
| 1 — Corrigir garantias | A01/A02/A03 e regressões; redação central; revisão/digest de validação | As três reproduções passam a rejeitar ou neutralizar o problema; suíte existente continua aprovada |
| 2 — Controlar execução e recuperação | A04/A07/A10; ambiente permitido, isolamento, deadline, journal/changeset, doctor/reparo | Comandos respeitam o escopo; cancelamento e crash têm desfecho verificável |
| 3 — Compatibilidade e escala | A05/A06/A08/A09/A11; capacidades por modelo, orçamento e trace eficientes | Benchmarks e fixtures por provedor mostram latência/consumo e protocolo corretos |
| 4 — Qualidade de release | A12/A13/A14; corpus real, matriz de OS, build/release, licença e componentes tipados | Release instalado em ambiente limpo e atividades reais com critérios de aceite objetivos |

O próximo trabalho recomendado é a etapa 1. As demais ampliam confiabilidade de forma gradual, com critérios observáveis e sem prometer que a IA garante a correção integral de um projeto.
