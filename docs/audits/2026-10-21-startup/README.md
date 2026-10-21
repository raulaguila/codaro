# Inicialização simplificada

Capturas da aplicação Textual real com projeto temporário e provedor simulado:

- [Primeiro acesso em 60×20](setup-60x20.png)
- [Preferências em 60×20](preferences-60x20.png)
- [Preferências em 120×35](preferences-120x35.png)
- [Limites avançados em 60×20](advanced-60x20.svg)

As ações de Salvar/Cancelar continuam visíveis nos dois tamanhos. O cadastro
guiado busca catálogo e metadados sem executar inferência. Preferências salvas
valem para novas inicializações e preservam as permissões da tarefa atual.

A suíte completa passou com 640 testes aprovados e um pulado. A verificação
posterior do ajuste de rascunhos passou em 61 testes de inicialização/UI. Os testes
novos de inicialização estão em `tests/test_startup.py`. Build, validação de
distribuição e smoke tests do pacote instalado também passaram.

Os PNGs foram renderizados a partir dos SVGs com Inkscape. Não foram testados
provedores reais ou terminais nativos de macOS/Windows nesta verificação.

Veja [Inicialização e configuração](../../STARTUP.md) para uso e precedência.
