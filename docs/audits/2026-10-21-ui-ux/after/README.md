# Validação posterior às correções

Os oito critérios da auditoria passaram na reprodução da interface Textual real:
[verification.json](verification.json). Foram registrados novamente 49 estados
na matriz de 60×20, 80×24, 120×35 e 160×50.

Validação final em Python 3.12/Linux:

- Suíte completa: **620 testes aprovados, 1 pulado** em 189,13 segundos.
- 18 regressões adicionais em `tests/test_ux_polish.py`.
- Ruff e formatação aprovados; `git diff --check` sem erros.
- Build do wheel e sdist, seguido de `twine check`, aprovado.
- Wheel instalado fora do checkout: abertura, estilos, paleta, comando local e
  apresentação de recuperação aprovados.

O teste pulado depende da disponibilidade do sandbox Linux/bubblewrap no ambiente;
essa limitação não foi contornada removendo proteções do aplicativo.

| Critério | Comportamento verificado |
| --- | --- |
| UX01 | Grupo recolhido oculta conteúdo e títulos de todas as etapas. |
| UX02 | Repetir mantém o novo rascunho. A regressão também cobre cursor e edições durante a nova geração. |
| UX03 | Esc cancela consultas e mantém controles acessíveis. As regressões de cadastro, edição, atualização e seleção comprovam que resultados tardios não gravam alterações. |
| UX04 | Sugestões mantêm pelo menos quatro linhas da conversa nas dimensões testadas, com descrições abreviadas e dica visível. |
| UX05 | Esc fecha sugestões e conserva o texto. |
| UX06 | Configurar provedor aparece na abertura em todas as dimensões. |
| UX07 | Cancelamento oferece somente Repetir e usa apresentação neutra. Autenticação, indisponibilidade e limites têm ações específicas. |
| UX08 | Sucesso na repetição recolhe a falha como resolvida e remove os botões antigos. |

O seletor de modelos também deixou de usar a propriedade reservada `loading` do
Textual como flag interna, que ocultava o formulário durante consultas. O estado
interno agora mantém o formulário, o progresso e o cancelamento visíveis.

As melhorias de apresentação incluem contexto compacto com detalhes em `/status`,
etapa e tempo decorrido na geração e campos avançados agrupados no cadastro.
Detalhes de revisão continuam acessíveis ao expandir a prévia com o teclado.

Capturas selecionadas:

- [Raciocínio recolhido](reasoning-collapsed.png)
- [Autocompletar em 60×20](completion-60x20.png)
- [Cancelamento neutro](cancel-after.png)
- [Cadastro compacto](provider-60x20.png)
- [Resposta final separada](stream-final.png)
- [Repetição resolvida, com rascunho mantido](retry-finished.txt)

Os SVGs e textos representam a tela real; os PNGs foram renderizados com Inkscape.
Os testes usam projetos e provedores sintéticos, sem chamadas externas. Não
equivalem a inferência real com Ollama/HPC ou a validação em terminais nativos
de macOS/Windows.
