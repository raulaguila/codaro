# Performance da interface e persistência

Análise local de 27/10/2026, Linux/Python 3.12/Textual 6.12, terminal 120×35.
Os dados são sintéticos e nenhuma medição chama modelos ou utiliza credenciais.
Os números abaixo são amostras indicativas de tempo real; variam com CPU,
terminal, disco e carga concorrente, e não são metas garantidas por máquina.

## Gargalos reproduzidos e correções

| Cenário | Antes | Depois |
| --- | ---: | ---: |
| Abrir histórico de 30 respostas, 8 blocos Markdown por resposta | 22.412 ms | 1.653–1.665 ms |
| Componentes montados nesse histórico | 3.490 | 130 |
| Ctrl+Q até encerrar o teste nesse histórico | 728 ms | 110–124 ms |
| Abrir histórico de 30 respostas, 40 blocos por resposta | excedeu a espera de montagem do teste | 4.050–5.400 ms |
| Salvar 50 respostas de 60.000 caracteres, reduzidas para 8 turnos/≈481 KB | 4.558 ms | 210–442 ms |

A comparação headless mede desde a entrada no `run_test` até a interface
estabilizar, e desde o envio de Ctrl+Q até sair do contexto do teste. Não mede
inicialização do interpretador, inferência nem latência da API. A linha de
armazenamento inclui validação, mascaramento, seleção dos turnos e escrita
atômica. Foram duas execuções após a alteração, sem tratamento estatístico;
não se deve interpretar diferenças de poucos milissegundos como significativas.

- **Histórico retomado:** Markdown com um componente por resposta, em vez de
  componentes separados por parágrafo, item e bloco. Montagem em lote e uma
  atualização de rolagem. Continuam visíveis até 30 turnos, como antes.
- **Conversa em andamento:** ao enviar outra pergunta, respostas anteriores às
  três mais recentes usam a mesma renderização leve. A ordem, o texto completo,
  blocos de código, decisões e ações de copiar/consultar são preservados.
  Essa mudança é visual; não compacta nem altera o contexto do modelo.
- **Persistência:** uma passagem de mascaramento por snapshot e busca binária
  pelo maior sufixo de turnos que cabe em 512 KB, mantendo o alinhamento das
  decisões. A gravação continua atômica; não se adia a persistência de respostas
  do usuário que precisam estar salvas antes de o agente continuar.
- **Saída:** Ctrl+Q sinaliza cancelamento antes da desmontagem do histórico.
  Comandos próprios, investigação, aquecimento de índice e listagem inicial
  recebem o sinal; callbacks tardios não atualizam a interface em encerramento.
- **Listagem inicial:** consulta ao ripgrep interrompível em intervalos de até
  50 ms, com coleta do processo e fechamento dos pipes, preservando ignores e
  política de arquivos. Outras consultas mantêm o comportamento anterior.
- **Comandos em segundo plano:** o encerramento compartilha um orçamento de
  espera de três segundos entre os jobs, em vez de três segundos por job.
  O sinal é enviado a todos antes de aguardar qualquer um.

## Validação

- Suíte completa local: **897 aprovados, 18 ignorados**. Os ignorados incluem
  provedores opcionais e casos que exigem sockets indisponíveis no sandbox.
- Sete regressões novas verificam quantidade de componentes, conteúdo e ações
  do histórico, decisões, cancelamento antes da desmontagem, término real de
  processo e descendente, orçamento compartilhado, listagem cancelável e
  retenção/mascaramento de sessões grandes com caracteres UTF-8.
- Ruff e formatação verificados.
- Suite adicional de HTTP local com IPC habilitado: **21 aprovados**, incluindo
  cancelamento de chamadas reais contra o servidor de teste.
- Teste adicional em terminal PTY real com 30 turnos: despacho de Ctrl+Q em
  **19 ms**, retorno da aplicação em **262 ms**, saída do processo Python em
  **697 ms**, código de saída zero. O terminal foi drenado continuamente pelo
  teste. Esse ensaio exige IPC local habilitado no ambiente de execução.

## Reproduzir

```bash
python scripts/benchmark_ui.py --output artifacts/performance/report.json
pytest tests/test_performance.py -q
```

O script usa um projeto e configuração descartáveis. O relatório contém
versões do ambiente, número de componentes, abertura, despacho/encerramento,
limpeza dos serviços e gravação de sessões em três tamanhos. Capturas SVG
sintéticas ficam ao lado do JSON. Compare a mesma máquina, dependências e carga.
O relatório desta rodada está em `artifacts/performance-2026-10-27/report.json`;
artefatos gerados não entram no controle de versão.

Os testes de regressão verificam trabalho limitado e preservação do comportamento,
sem impor tempos de CPU que tornariam o CI instável. A suíte de performance não
substitui a aceitação com provedores reais nem avalia velocidade de inferência.

## Limites restantes

Respostas Markdown muito extensas ainda custam tempo de análise e layout, mesmo
com menos componentes. A persistência continua síncrona e inclui sincronização
no disco; arquivos válidos muito grandes ou disco lento podem causar pausas.
Aquecimento vetorial e operações nativas já iniciadas mantêm encerramento
cooperativo e seus limites de espera. Não se encerra o Python abruptamente para
ocultar espera, nem se abandona a coleta dos processos do teste.
