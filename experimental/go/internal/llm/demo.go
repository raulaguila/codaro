package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// Demo exercises real read-only tools without calling a model or requiring credentials.
type Demo struct{}

func (Demo) Catalog(context.Context) ([]Model, error) {
	return []Model{{ID: "demo-local", Context: 8192, Source: "demonstração"}}, nil
}
func (Demo) Describe(context.Context, string) (Model, error) {
	return Model{ID: "demo-local", Context: 8192, Source: "demonstração"}, nil
}
func (Demo) Chat(ctx context.Context, messages []Message, tools []Tool, emit func(Delta)) (Response, error) {
	last := messages[len(messages)-1]
	makeCall := func(name, args string) Response {
		return Response{Calls: []Call{{ID: fmt.Sprintf("demo_%d", callSequence.Add(1)), Type: "function", Function: Function{Name: name, Arguments: args}}}, Finish: "tool_calls"}
	}
	if last.Role != "tool" {
		emit(Delta{Reasoning: "Demonstração: vou listar os arquivos e ler uma fonte real do diretório selecionado."})
		return makeCall("list_files", `{"limit":30}`), nil
	}
	if last.Name == "list_files" {
		var result struct {
			Files []string `json:"files"`
		}
		_ = json.Unmarshal([]byte(last.Content), &result)
		selected := ""
		for _, want := range []string{"README.md", "go.mod", "pyproject.toml", "package.json"} {
			for _, name := range result.Files {
				if name == want {
					selected = name
					break
				}
			}
			if selected != "" {
				break
			}
		}
		if selected == "" && len(result.Files) > 0 {
			selected = result.Files[0]
		}
		if selected != "" {
			return makeCall("read_lines", Encode(map[string]any{"path": selected, "start": 1, "end": 12})), nil
		}
	}
	text := "**Demonstração local — sem modelo de IA.**\n\nO fluxo listou os arquivos permitidos usando as ferramentas reais. Configure um provedor em `/providers` para conversar com um modelo."
	if last.Name == "read_lines" {
		var data struct {
			Path    string `json:"path"`
			Content string `json:"content"`
		}
		_ = json.Unmarshal([]byte(last.Content), &data)
		if data.Path != "" {
			text = fmt.Sprintf("**Demonstração local — sem modelo de IA.**\n\nListei os arquivos permitidos e li `%s` no diretório escolhido. Abaixo está uma prévia real:\n\n```text\n%s\n```\n\nUse `/providers` para conectar Ollama ou uma API compatível com OpenAI. `/process` mostra as etapas deste teste.", data.Path, strings.TrimSpace(data.Content))
		}
	}
	if len(messages) > 0 && strings.Contains(messages[0].Content, "Modo Planejar") {
		text = "**Demonstração de planejamento — sem modelo de IA.**\n\nConsultei arquivos reais com as ferramentas de leitura. Um plano de demonstração seria:\n\n1. Confirmar o objetivo e os arquivos envolvidos.\n2. Definir a alteração e seus critérios de aceitação.\n3. Implementar em uma etapa futura, com autorização.\n4. Executar as verificações adequadas e revisar o resultado.\n\nNenhum arquivo foi alterado e nenhuma verificação foi executada. Conecte seu modelo em `/providers` para gerar um plano específico."
	}
	for _, word := range strings.SplitAfter(text, " ") {
		select {
		case <-ctx.Done():
			return Response{}, ctx.Err()
		case <-time.After(15 * time.Millisecond):
			emit(Delta{Content: word})
		}
	}
	return Response{Content: text, Finish: "stop"}, nil
}
