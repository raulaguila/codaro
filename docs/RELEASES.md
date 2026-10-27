# Distribuição, atualização e compatibilidade

A versão 0.2.0 permanece alpha. Python 3.11–3.13 faz parte do CI; a distribuição
instalada é exercitada em Linux, macOS e Windows com Python 3.12. `ripgrep` deve
estar no PATH. Gramáticas são distribuídas pelo pacote fixado em `pyproject.toml`.
LSP, compiladores e runtimes dos projetos são dependências opcionais externas.

## Instalar e atualizar

Até existir publicação no PyPI, use um wheel da release ou o código revisado:

```bash
pipx install ./codaro-0.2.0-py3-none-any.whl
# Na raiz de um checkout:
pipx install .
# Atualização de uma instalação pipx existente, após baixar o novo wheel:
pipx install --force ./codaro-0.2.0-py3-none-any.whl
```

Verifique `SHA256SUMS` antes de instalar o artefato baixado. `codaro --version`
e `codaro doctor` conferem versão e requisitos locais; não comprovam inferência.
Execute os testes de conexão/modelo na interface para seu provedor.

Extras opcionais: `.[semantic]`, `.[tokenizer]` e `.[credentials]`. O último
habilita `keyring`; instalar a dependência não configura um cofre no sistema.
No Linux é necessário Secret Service/KWallet disponível e desbloqueado. macOS usa
Keychain; Windows usa o backend nativo do keyring. Backends de texto simples são
recusados. Sem cofre disponível, a migração falha e mantém a configuração anterior.

## Dados locais e migração de 0.1.x

Feche o Codaro e faça uma cópia privada de `.codaro/` e da configuração de usuário
antes de atualizar. Não versione nem envie essas cópias: podem conter código e
credenciais. Não há replay de comandos ao restaurar a conversa.

- O índice SQLite passa ao schema 6; versões conhecidas são reconstruídas como
  cache. Memória, sessões, artefatos e checkpoints não são apagados.
- Tarefas continuam no formato 1. Novos vínculos `criterion_checks` e recibos
  `cwd`, `check_id`, `job_id` e revisão são aditivos.
- Perfis continuam no formato 1 enquanto as chaves ficam em arquivo. A migração
  explícita ao cofre usa formato 2, com referência opaca e chave vazia no JSON.
  `codaro providers credentials NOME --storage keyring` faz a migração;
  `--storage file` a desfaz após recuperar a chave. Não há fallback automático.
- Para voltar a 0.1.x, migre primeiro todos os perfis do cofre para arquivo, ou
  restaure uma cópia privada anterior. 0.1.x não interpreta perfis de formato 2.
- `codaro providers export` exporta metadados sem chaves nem referências do cofre.
  O cofre pertence ao usuário/sistema e não acompanha esse arquivo.

Enquanto o projeto for 0.x, mudanças incompatíveis exigem incremento da versão
menor, instruções de migração e changelog. Correções compatíveis usam a versão
patch. Imports públicos de compatibilidade continuam mantidos nesta versão;
novos módulos devem usar os pacotes canônicos de `docs/ARCHITECTURE.md`.

## Preparar uma release

1. Atualize `pyproject.toml`, `src/codaro/__init__.py` e `CHANGELOG.md` juntos.
2. Execute lint, testes, avaliações de recuperação e aceitação completa com o
   modelo/provedor que pretende recomendar. Guarde JUnit e métricas. O smoke do
   protocolo sozinho não qualifica um modelo para implementar tarefas.
3. Execute o workflow `release` manualmente para revisar distribuições, checksums
   e smoke instalado nas três plataformas. A gate de protocolo usa Ollama local,
   sem API paga, e pode falhar por disponibilidade ou comportamento do modelo.
4. Após revisão, uma tag `vVERSÃO` no commit validado aciona a pré-release no
   GitHub. Commit/push em `develop` não cria tag nem publica uma distribuição.

A publicação PyPI está preparada, mas **desabilitada por padrão**. Antes de ativar
`CODARO_PUBLISH_PYPI=true` nas variáveis do repositório, o mantenedor precisa
registrar o Trusted Publisher no PyPI para `raulaguila/codaro`, workflow
`release.yml`, environment `pypi`, e configurar os revisores desse environment.
O job usa OIDC e depende da pré-release e das verificações; nenhuma chave PyPI
é armazenada no repositório. A configuração da conta não é feita pelo workflow.

## Limites da execução

Lotes de edição fazem preflight e tentam rollback se uma aplicação gerenciada
falhar. Isso não é uma transação do filesystem: queda de energia/processo ou uma
alteração externa pode impedir reversão completa. Checkpoints e recibos mostram
os arquivos aplicados/revertidos e conflitos que exigem revisão.

No Windows, leitura/edição usa handles Win32, recusa reparse points/hard links e
preserva bytes UTF-8/BOM/CRLF. Processos pertencem a Job Objects antes de começar;
fechar/cancelar encerra descendentes. Essa propriedade não é sandbox de acesso.
O isolamento opcional Bubblewrap continua restrito a Linux.
