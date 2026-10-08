# Plataforma do agente

As nove etapas do plano têm uma implementação inicial: contexto central,
artefatos, continuidade, sessões/reversões, exploração delegada, MCP, plugins,
diagnósticos LSP e avaliação de implementação. Este documento explica seus controles
e limites. Os testes locais usam modelos/respostas controlados, não todos os
provedores reais.

## Ativação

Artefatos ficam habilitados por padrão. Compactação determinística e recuperação
de erros de contexto continuam automáticas. Resumos semânticos, exploração e LSP
são opcionais; MCP/plugins precisam de cadastro e confiança explícita.

~~~bash
codaro features show
codaro features set semantic_compaction true
codaro features set exploration true
codaro features set lsp true
~~~

No chat: /features semantic_compaction on, /features exploration on e /features
lsp on. Configuração: .codaro/features.json, no projeto. Reinicie outras instâncias
abertas após mudar a configuração pela CLI.

O orçamento considera o payload convertido pelo adaptador do provedor, incluindo
mensagens, instruções, schemas, reserva de resposta e calibração disponível.
get_tools_catalog lista capacidades em páginas; request_tools carrega os nomes
para a próxima requisição. Descoberta não concede autorização.

## Continuidade e artefatos

Com semantic_compaction ativo, o cliente tenta resumir histórico descartado e
lotes antigos antes de atingir o limite. Mantém uma cauda recente quando cabe.
O resumo contém objetivo, detalhes/decisões, concluído, em andamento, bloqueios,
próximos passos e arquivos. Usa o mesmo provedor/modelo; seleção independente de
um modelo de compactação ainda não está disponível.

A síntese é validada, limitada, mascarada com as chaves conhecidas e armazenada por
sessão. Se falhar, a compactação determinística continua. Orientações obrigatórias,
restrições do usuário e aprovações não são substituídas pela síntese. Trechos
removidos precisam ser relidos antes de autorizar alterações.

O padrão reserva 12% da entrada para compactação preventiva e tenta destinar 25%
à cauda recente. context_reserve_ratio e recent_ratio aceitam valores entre 0,05
e 0,4. São até três tentativas de síntese por interação, com até três requisições
por tentativa.

Saídas grandes recebem artifact_id. Comandos capturam até 2 MB, mantendo a mensagem
ao modelo limitada. get_artifact_info, read_artifact e search_artifact recuperam
páginas/trechos sem repetir comandos. IDs permanecem no registro compactado e na
memória da conversa. Artefatos não comprovam o estado atual dos arquivos.

Limites: 2 MB por artefato, 32 MB no conjunto, 100 arquivos e sete dias de retenção.
O resultado indica captura incompleta. IDs de outra sessão não são recuperáveis.

.codaro/prompt.json continua sendo o diagnóstico principal; auxiliary_requests
contém requisições de síntese sem substituir a iteração principal. Exploração usa
.codaro/exploration.json e seu próprio run-*.jsonl. Dumps/arquivos incrementais
têm cotas e retenção. Chaves conhecidas são mascaradas; outros segredos presentes
no código não são detectados automaticamente. Mantenha .codaro fora do Git.

## Sessões e reversões

~~~bash
codaro sessions list
codaro sessions new "Corrigir autenticação"
codaro sessions use IDENTIFICADOR
codaro . --resume
codaro undo-turn
codaro undo-turn --run-id IDENTIFICADOR_DO_FLUXO
codaro redo
~~~

No chat: /sessions, /session new TÍTULO e /session IDENTIFICADOR. Há até 30 sessões.
A original, default, preserva os arquivos existentes sem migração destrutiva.
Cada nova sessão tem conversa, memória, tarefa e resumo próprios. Índice e código
pertencem ao projeto e são compartilhados. Trocar sessão revoga permissões e
observações de leitura; não reaplica operações.

/undo continua revisando uma edição individual. /undo-turn e /redo revisam todos
os arquivos da interação. Reversões consolidam escritas repetidas e conferem todos
os arquivos antes da primeira alteração. Mudança manual incompatível bloqueia a
operação. Erro durante aplicação pode deixar resultado parcial: intenção/caminhos
aplicados ficam em .codaro/reversals.json. Vários arquivos não formam uma transação
atômica do sistema de arquivos.

Snapshots: 20 itens/12 MB; grupos incompletos são removidos pela retenção. Não
desfaz efeitos de comandos, serviços ou ferramentas externas. No chat, histórico
permanece com anotação; contexto e permissões são reiniciados. Pela CLI, a conversa
ativa é reiniciada; memória/diagnósticos históricos permanecem.

## Exploração delegada

explore_code investiga somente por leitura, com até seis etapas, contexto próprio
e prazo de até 120 segundos ou o restante do pai. O filho não recebe edição,
comandos, MCP, plugins ou outra exploração. Seu relatório é limitado a 3.000
caracteres e não autoriza edição.

Pai, sínteses e filhos compartilham max_run_requests (padrão 64) e max_run_tokens
(padrão 1.000.000). É estimativa conservadora de entrada mais reserva de saída por
requisição lógica, não cobrança real. Retentativas HTTP permanecem sujeitas aos
limites próprios do transporte. O filho reserva uma requisição e a janela do pai
para sua resposta. Cancelamento/prazo são propagados. Se a exploração não couber,
o pai recebe o resultado de falha e pode continuar diretamente.

## MCP

Ctrl+P → **Cadastrar MCP ou plugin** abre formulário com teste de conexão.
O teste exige confiança explícita, inicializa só o cadastro em edição e lista
ferramentas, sem salvar o cadastro nem chamar o modelo.

~~~bash
codaro integrations add mcp local \
  --command '["/caminho/do/servidor", "--stdio"]' --trust
codaro integrations add mcp remoto --url https://servidor.example/mcp \
  --token-env MCP_TOKEN --trust
codaro integrations test
codaro integrations remove mcp local
~~~

--tls-insecure existe para MCP HTTP, além do controle existente dos provedores BYOK.
Credenciais remotas vêm de variáveis. --env NOME permite variáveis específicas para
processos; valores não ficam na configuração.

Transportes: stdio JSON-RPC por linha e HTTP Streamable com JSON/SSE. Negocia
2024-11-05, 2025-03-26 ou 2025-06-18. Não oferece SSE legado nem resources, prompts,
roots ou sampling. Paginação, frames, schemas, catálogo, prazo e saídas têm limites.
Nomes recebem prefixo de origem/hash, evitando colisões com ferramentas internas.

--read-only NOME_ORIGINAL declara explicitamente uma ferramenta confiável de
leitura. Anotações do servidor não concedem esse privilégio. Outras ferramentas
só aparecem em Executar e exigem revisão por ação de origem, nome e argumentos.
Escopo de arquivos/comandos não dispensa essa revisão. Ask/Plan não recebem
ferramentas externas mutáveis.

Schemas externos suportam objetos, arrays, escalares, enum, obrigatoriedade,
propriedades adicionais e limites. Referências, regex, uniões e combinadores não
são suportados. Um catálogo incompatível fica indisponível com diagnóstico, sem
ignorar restrições. Nenhum schema remoto é baixado.

## Plugins

~~~bash
codaro integrations add plugin exemplo --plugin ./meu_plugin.py \
  --trust --read-only somar
# Exemplo pronto neste repositório:
codaro integrations add plugin matematica --plugin examples/plugins/math.py \
  --trust --read-only somar
~~~

A API Python v1 expõe register() com api_version: 1, listas tools e commands,
e on_event opcional. Entradas declaram name, description, parameters e
handler(arguments). Comandos são expostos no catálogo de ferramentas, seguindo
as mesmas permissões; não há comandos slash específicos para plugins.
Não há instalação automática nem descoberta de plugins a partir do repositório.

Eventos: run_started, tool_completed para ferramentas externas e run_finished.
Não modificam prompts nem concedem permissões. Cada integração usa um host separado,
protocolo limitado e encerramento ao fim da atividade. O hash do arquivo de entrada
é novamente verificado no host, que executa os bytes verificados. Atualizar requer
remover o cadastro e renovar a confiança. Dependências não são cobertas pelo hash.

Um processo separado **não é uma sandbox do sistema operacional**. Confie apenas
em código/serviços que aceita executar. Chaves BYOK não são passadas automaticamente
ao ambiente dos processos; variáveis explicitamente autorizadas podem conter
credenciais e passam a integrar o mascaramento conhecido.

## LSP opcional

get_diagnostics consulta o arquivo atual via servidor instalado:
pyright-langserver --stdio para Python ou typescript-language-server --stdio para
TypeScript/TSX. Ative lsp e instale o servidor por conta própria; não há downloads
silenciosos. Comandos alternativos são listas em lsp.servers.

Inicialização/didOpen usam o projeto atual. Diagnósticos por push são limitados,
vinculados a hash/versão e marcados obsoletos se o arquivo muda. Servidor ausente
ou publicação pendente têm retorno próprio; lista pendente vazia não significa
validação aprovada. Arquivos acima de 200 KB não são enviados ao diagnóstico.
LSP não substitui testes/typecheck; referências, definição e workspace symbols
ainda não estão implementados.

Quando habilitado, alterações aprovadas consultam até dois arquivos modificados
para oferecer retorno de diagnósticos ao modelo. Esse retorno não marca a tarefa
como validada e permanece sujeito ao prazo da atividade.

## Avaliação de implementação

codaro evaluate mantém avaliação de busca/investigação. O novo comando executa
atividades em cópias temporárias:

~~~bash
codaro evaluate-workflows casos.json --allow-execution --output resultado.json
~~~

~~~json
[
  {
    "id": "corrigir-validacao",
    "repo": "fixtures/projeto",
    "query": "Corrija a validação e execute os testes.",
    "expected_files": {"auth.py": ["if user is None"]},
    "validation_commands": [["python", "-m", "pytest", "-q"]]
  }
]
~~~

Caminho relativo ao arquivo de casos. Até 30 casos/256 KB, 2.000 arquivos permitidos
e 20 MB por cópia. Só aprova alterações nos caminhos esperados e comandos exatamente
declarados. Confere arquivos reais e executa validações depois do agente; não altera
o projeto original. Código/comandos do fixture não são uma sandbox: use
--allow-execution apenas para casos confiáveis.

Relatórios trazem expectativas, códigos de saída, tempo, chamadas, compactações e
usage quando informado. Custo sem preço conhecido é null. Integrações, exploração
e síntese ficam desativadas no benchmark inicial para limitar variação/efeitos.
Testes automatizados usam servidores locais falsos e HTTP controlado; não medem
qualidade real de todos os modelos/provedores.
