# Segurança

O Codaro está em estágio alpha. Atualize para a versão mais recente antes de
relatar uma falha. Para vulnerabilidades que exponham credenciais, contornem
aprovações ou permitam executar código sem autorização, use o canal privado
de GitHub Security Advisories quando habilitado no repositório. Não publique
chaves, arquivos `.env`, dumps integrais ou código confidencial em issues.

Inclua versão, sistema, provedor/protocolo, passos mínimos e impacto. Remova
informações sensíveis do diagnóstico antes de compartilhar. Ainda não há SLA
de resposta ou programa de recompensa.

## Fronteiras de execução

- Aprovações controlam ferramentas oferecidas ao agente. Um comando autorizado
  pode executar código do projeto, incluindo seus testes e dependências.
- Por padrão, comandos não recebem variáveis com nomes indicativos de chaves,
  tokens, senhas ou credenciais. `CODARO_COMMAND_ENV` libera nomes explícitos;
  `CODARO_API_KEY` nunca é repassada por esse mecanismo.
- `CODARO_SANDBOX=required` exige Linux e Bubblewrap. Isola rede/processos,
  oferece runtimes de sistema somente para leitura, o projeto para escrita e
  diretórios temporários próprios. Se a inicialização falhar, não há retentativa
  automática fora da sandbox. Dependências fora desses caminhos podem faltar.
- O modo padrão, `CODARO_SANDBOX=off`, executa com os acessos do usuário.
  macOS e Windows ainda não possuem adaptador de isolamento do Codaro.
- MCP, plugins e servidores LSP têm processos e políticas próprios; a sandbox
  dos comandos não os isola. Só cadastre integrações confiáveis.
- Snapshots e undo/redo cobrem arquivos, não efeitos externos de comandos.
- O diagnóstico local registra código e conversa. O mascaramento de chaves
  conhecidas não detecta todo segredo contido nesses dados. Preserve `.codaro/`
  fora do Git e revise o conteúdo antes de compartilhá-lo.
