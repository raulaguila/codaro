package agent

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

type Task struct {
	Title  string `json:"title"`
	Status string `json:"status"`
}
type Question struct {
	Text    string   `json:"question"`
	Options []string `json:"options,omitempty"`
}

func interactionTools() []llm.Tool {
	def := func(name, desc string, props map[string]any, required ...string) llm.Tool {
		return llm.Tool{Type: "function", Function: llm.Definition{Name: name, Description: desc, Parameters: map[string]any{"type": "object", "properties": props, "required": required, "additionalProperties": false}}}
	}
	return []llm.Tool{
		def("ask_user_question", "Pergunta ao usuário somente quando falta uma decisão necessária. Aguarda resposta; opções são sugestões, texto livre sempre permitido.", map[string]any{"question": map[string]any{"type": "string", "maxLength": 500}, "options": map[string]any{"type": "array", "maxItems": 4, "items": map[string]any{"type": "string", "maxLength": 160}}}, "question"),
		def("update_plan", "Cria ou atualiza a lista completa de etapas de investigação/planejamento. Use estados reais; nunca marque implementação/testes como concluídos nesta versão somente leitura.", map[string]any{"tasks": map[string]any{"type": "array", "minItems": 1, "maxItems": 12, "items": map[string]any{"type": "object", "properties": map[string]any{"title": map[string]any{"type": "string", "maxLength": 160}, "status": map[string]any{"type": "string", "enum": []string{"pending", "in_progress", "completed", "cancelled"}}}, "required": []string{"title", "status"}, "additionalProperties": false}}}, "tasks"),
	}
}

func ParsePlan(raw string) ([]Task, error) {
	var doc struct {
		Tasks []Task `json:"tasks"`
	}
	if len(raw) > 8000 || json.Unmarshal([]byte(raw), &doc) != nil || len(doc.Tasks) < 1 || len(doc.Tasks) > 12 {
		return nil, errors.New("informe de 1 a 12 etapas")
	}
	inProgress := 0
	for _, task := range doc.Tasks {
		if strings.TrimSpace(task.Title) == "" || len([]rune(task.Title)) > 160 {
			return nil, errors.New("título inválido")
		}
		switch task.Status {
		case "pending", "completed", "cancelled":
		case "in_progress":
			inProgress++
		default:
			return nil, errors.New("estado de etapa inválido")
		}
	}
	if inProgress > 1 {
		return nil, errors.New("somente uma etapa pode estar em andamento")
	}
	return doc.Tasks, nil
}

func (a *Agent) interact(ctx context.Context, call llm.Call, emit func(Event)) (map[string]any, error) {
	if call.Function.Name == "update_plan" {
		tasks, err := ParsePlan(call.Function.Arguments)
		if err != nil {
			return nil, err
		}
		emit(Event{Kind: "plan", Tasks: tasks})
		return map[string]any{"tasks": tasks, "saved": true}, nil
	}
	var q Question
	if len(call.Function.Arguments) > 4000 || json.Unmarshal([]byte(call.Function.Arguments), &q) != nil || strings.TrimSpace(q.Text) == "" || len([]rune(q.Text)) > 500 || len(q.Options) > 4 {
		return nil, errors.New("pergunta inválida; até 4 opções e 500 caracteres")
	}
	for _, option := range q.Options {
		if strings.TrimSpace(option) == "" || len([]rune(option)) > 160 {
			return nil, errors.New("opção inválida")
		}
	}
	reply := make(chan string, 1)
	emit(Event{Kind: "question", Question: &q, Reply: reply})
	select {
	case answer := <-reply:
		return map[string]any{"answer": answer}, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}
