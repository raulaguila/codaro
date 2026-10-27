# Verificação de maturidade — 0.2.0

Escopo: correção LSP personalizada, edição Windows, lotes revisados, jobs,
validação por revisão, continuidade, reutilização de serviços, organização e
preparação de distribuição. Dados de modelo foram somente fixtures sintéticas.

## Evidência local

- Ruff check e format: aprovados; `pip check`: sem dependências quebradas.
- Suíte final: **873 aprovados, 18 pulados**, em 233 s. Os pulados incluem
  aceitação real opt-in e dependências/condições opcionais da suíte padrão.
- Recuperação: **19/19** casos versionados.
- Cinco fixtures de implementação: replay pelo agente com edição aprovada e
  execução real de testes Python, Go 1.23.12 e Node, todas aprovadas. O replay
  usa respostas programadas; não mede a capacidade de implementar do modelo.
- Wheel e sdist 0.2.0: build, `twine check` e instalação fora do checkout aprovados.
  Smoke de CLI/imports/CSS/agente/UI e três testes de edição/undo/conflito/processos
  aprovados no Linux.
- Cofre: backend injetado testa migração, exportação, bloqueio, falha de gravação
  e preservação da referência antiga. Não foi aberto um cofre nativo real aqui.

## Inferência real

Ollama 0.12.3 em contêiner descartável, 2 CPUs/4 GB, contexto de 8192 tokens,
sem API paga. Resultado exato da execução completa anterior à revisão final:

| Modelo / escopo | Resultado |
| --- | --- |
| Llama 3.2 1B: catálogo, stream e ciclo de ferramenta | 2/3; falha na conclusão do ciclo |
| Qwen 2.5 3B: mesmo smoke | 3/3 |
| Qwen 2.5 3B: aceitação completa | 3 aprovados, 5 falharam; 1433 s |
| Qwen 2.5 3B: repetição do smoke após ajustes finais | 3/3; 82 s |
| Qwen 2.5 3B: endpoint OpenAI-compatible do mesmo Ollama | 3/3; 47 s |

Na aceitação completa, o cenário de análise retornou sem a leitura de arquivo
esperada; a decisão restaurada não apareceu na resposta; recuperação de histórico,
leitura paginada e três tarefas de implementação atingiram prazos ou falharam.
Não foram relaxadas expectativas nem aumentado o limite para transformar essas
falhas em aprovação. Os resultados não qualificam esse modelo/ambiente para
execução autônoma. JUnit registra os cenários em novas execuções do workflow live.

## Limites de verificação

Os testes do pacote instalado, incluindo edição, undo, conflito e processos,
passaram no CI nativo em Windows, macOS e Linux no commit `388120a`.
Na primeira execução do CI, Linux/macOS passaram; o teste nativo Windows detectou
separadores do ripgrep não normalizados na criação de subpastas. A comparação
passou a normalizar os caminhos com `Path.as_posix()`, mantendo o teste que
reproduziu a falha como verificação obrigatória.
A matriz Python também detectou animação ainda em andamento ao usar início/fim
da lista do plano. Esses saltos passaram a ser imediatos, preservando a escolha
de rolagem manual após atualizações do plano.
APIs pagas e cofres nativos não foram testados com credenciais nesta execução.
A publicação PyPI continua desabilitada até configuração administrativa.
Rollback gerenciado não promete recuperação automática após queda abrupta ou
conflito externo. Consulte `docs/RELEASES.md` para migração e limites operacionais.
