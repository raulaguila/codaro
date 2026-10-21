# Inicialização e configuração

O caminho comum de uso é `codaro`. Ele abre a interface no projeto atual usando o
perfil de modelo e as preferências salvas. Não é necessário repetir URL, contexto,
timeout ou limites do agente na linha de comando.

```sh
codaro
codaro ./meu-projeto
codaro --resume
codaro --mode plan
codaro ./meu-projeto --mode ask --no-resume
```

## Primeiro acesso

Sem provedor configurado, a interface abre o cadastro guiado. O usuário escolhe o
provedor e informa a chave; URL personalizada, TLS e teste de ferramentas ficam
nas opções de conexão. **Testar e continuar** consulta o catálogo antes de salvar.
Na tela seguinte, selecionar o modelo e clicar em **Começar** busca os metadados
do modelo e salva a seleção. Não há inferência paga automática nesse fluxo. O
teste de inferência/ferramentas é opcional e informa a possibilidade de custo.

Se já houver perfis, mas faltar um modelo ativo, o fluxo começa na seleção.
Cancelar devolve ao chat sem chamar um modelo fictício. O cadastro continua
acessível por Ctrl+P; enviar uma pergunta enquanto falta seleção reabre a
configuração.

A abertura usa o catálogo persistido e não faz uma consulta de rede bloqueante.
**Atualizar API** e a seleção de modelo buscam metadados atuais. Quando a API não
informa o contexto, o fallback conservador permanece identificado. O ajuste
automático após rejeições do servidor continua disponível.

## Preferências persistentes

Em **Ctrl+P → Preferências**, ou `/settings`, configure:

- Modo inicial: Perguntar, Planejar ou Executar.
- Retomar a última conversa ao abrir.
- Em **Limites avançados**: etapas, duração da atividade, tentativas de correção e
  timeout HTTP. Etapas em branco usam o padrão de cada modo.

Essas mudanças valem para novas inicializações. Não alteram o modo, os grants ou
as permissões da tarefa atual. Provedor/modelo/TLS são preferências do cadastro de
provedores; não há chaves no arquivo de preferências.

Também é possível configurar pela CLI:

```sh
codaro config show
codaro config set mode plan
codaro config set resume true
codaro config set max_steps auto
codaro config set max_seconds 1800
codaro config set max_corrections 3
codaro config set timeout 90
codaro config reset
```

`reset` pede confirmação, restaura apenas os padrões de inicialização e preserva
provedores, credenciais e dados de projetos. Automação pode usar `--yes`.

As preferências ficam em `$XDG_CONFIG_HOME/codaro/preferences.json`, normalmente
`~/.config/codaro/preferences.json`. A gravação é validada, atômica e serializada
por lock. As credenciais continuam em `provider-credentials.json`, com as
proteções existentes. Não são adicionados arquivos de configuração aos projetos.

## Precedência

| Configuração | Ordem de resolução |
| --- | --- |
| Provedor | `--provider` → `CODARO_PROVIDER` → perfil ativo salvo → URL/modelo do ambiente por compatibilidade → cadastro guiado |
| Modo, retomada e limites do agente | Flags do comando → flags de abertura → preferências salvas → padrões |
| Contexto e TLS | Flag explícita → variável avançada correspondente → metadados/configuração do perfil → fallback |
| Timeout | `CODARO_TIMEOUT` → preferência salva → 90 segundos |
| Limite de saída | `CODARO_MAX_OUTPUT_TOKENS`, se definido; padrão Automático |

Flags de inicialização são temporárias. Selecionar um perfil com `--provider`
não grava uma nova seleção global. Já a seleção de modelo na UI ou em
`codaro models use` é persistente.

Variáveis antigas de URL/modelo/chave não substituem um perfil salvo. Para usar
deliberadamente essas variáveis em scripts, selecione o modo `env`:

```sh
CODARO_PROVIDER=env CODARO_BASE_URL=http://localhost:11434/v1 CODARO_MODEL=qwen2.5:7b codaro ask "Explique o projeto"
```

`env` é reservado para esse modo nas novas configurações de perfis. As variáveis
avançadas de contexto, saída, TLS, tokenização e timeout continuam disponíveis.
`codaro config show` e `codaro doctor` identificam a origem da configuração e as
variáveis de conexão ignoradas, sem imprimir suas chaves. O reset das preferências
também permite recuperar um arquivo de preferências inválido.

## Ajuda e compatibilidade

`codaro --help` destaca uso diário e configuração. `codaro --help-all` também
mostra busca local, memória, tarefas, integrações e avaliações. A instalação de
completions continua disponível nas opções completas. Os comandos anteriores
permanecem aceitos, incluindo `codaro .`, `codaro chat --repo`, `edit`, `search`,
`index` e as flags avançadas de `chat` e `execute`.

O resolvedor fica em `codaro.configuration`; preferências, em `codaro.preferences`.
`Settings.from_env()` mantém sua API e delega a esse mesmo resolvedor. Chat e
comandos de perguntas usam os mesmos padrões de timeout e limites de execução.
Os subcomandos `ask`, `plan` e `execute` mantêm seus modos explícitos.

## Verificação

`tests/test_startup.py` cobre abertura, flags antes/depois do caminho, precedência,
origem sem segredos, preferências, recuperação de arquivo inválido, inicialização
sem rede, cadastro guiado e cancelamento sem inferência. Também verifica que salvar
preferências preserva o rascunho e as permissões da tarefa aberta. Os provedores
desses testes são simulados; não são feitas chamadas externas.

Validação desta implementação: 640 testes aprovados e um teste de isolamento
Linux pulado por limitação do executor. Depois do ajuste final de preservação do
rascunho no cadastro, foram aprovados mais 61 testes de inicialização, provedores
e chat. Ruff, formatação, build e `twine check` passaram. O wheel foi instalado
fora do checkout e verificado quanto a inicialização sem argumentos, preferências,
cadastro guiado, estilos, CLI, agente e diagnóstico.
