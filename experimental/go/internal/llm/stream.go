package llm

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"sync/atomic"
)

const MaxStreamBytes = 16 * 1024 * 1024

var callSequence atomic.Uint64

func finishCalls(calls []Call) ([]Call, error) {
	for i := range calls {
		if calls[i].ID == "" {
			calls[i].ID = fmt.Sprintf("call_%d", callSequence.Add(1))
		}
		calls[i].Type = "function"
		if calls[i].Function.Arguments == "" {
			calls[i].Function.Arguments = "{}"
		}
		if len(calls[i].Function.Arguments) > 64000 {
			return nil, fmt.Errorf("argumentos da ferramenta excedem 64 KiB")
		}
	}
	return calls, nil
}

func (c *Client) openai(ctx context.Context, messages []Message, tools []Tool, emit func(Delta)) (Response, error) {
	body := map[string]any{"model": c.Settings.Model, "messages": messages, "stream": true}
	if len(tools) > 0 {
		body["tools"] = tools
		body["tool_choice"] = "auto"
	}
	resp, err := c.request(ctx, "chat/completions", body)
	if err != nil {
		return Response{}, err
	}
	defer resp.Body.Close()
	if strings.Contains(resp.Header.Get("Content-Type"), "application/json") {
		var doc struct {
			Choices []struct {
				Message struct {
					Content   string `json:"content"`
					Reasoning string `json:"reasoning_content"`
					Calls     []Call `json:"tool_calls"`
				} `json:"message"`
				Finish string `json:"finish_reason"`
			} `json:"choices"`
			Usage struct {
				Input  int `json:"prompt_tokens"`
				Output int `json:"completion_tokens"`
			} `json:"usage"`
		}
		if err = readJSON(resp.Body, &doc); err != nil {
			return Response{}, err
		}
		if len(doc.Choices) == 0 {
			return Response{}, fmt.Errorf("provedor não retornou choices")
		}
		x := doc.Choices[0]
		calls, e := finishCalls(x.Message.Calls)
		emit(Delta{Content: x.Message.Content, Reasoning: x.Message.Reasoning})
		return Response{Content: x.Message.Content, Reasoning: x.Message.Reasoning, Calls: calls, Finish: x.Finish, Usage: Usage{doc.Usage.Input, doc.Usage.Output}}, e
	}
	var out Response
	var content, reasoning strings.Builder
	calls := map[int]*Call{}
	ended := false
	err = scanStream(resp.Body, true, func(data string) error {
		if data == "[DONE]" {
			ended = true
			return io.EOF
		}
		var doc struct {
			Choices []struct {
				Index int `json:"index"`
				Delta struct {
					Content   string `json:"content"`
					Reasoning string `json:"reasoning_content"`
					Thinking  string `json:"reasoning"`
					Calls     []struct {
						Index    int      `json:"index"`
						ID       string   `json:"id"`
						Function Function `json:"function"`
					} `json:"tool_calls"`
				} `json:"delta"`
				Finish string `json:"finish_reason"`
			} `json:"choices"`
			Usage struct {
				Input  int `json:"prompt_tokens"`
				Output int `json:"completion_tokens"`
			} `json:"usage"`
			Error json.RawMessage `json:"error"`
		}
		if err := json.Unmarshal([]byte(data), &doc); err != nil {
			return fmt.Errorf("evento SSE inválido: %w", err)
		}
		if len(doc.Error) > 0 && string(doc.Error) != "null" {
			return fmt.Errorf("o provedor enviou erro durante o stream")
		}
		if doc.Usage.Input > 0 {
			out.Usage = Usage{doc.Usage.Input, doc.Usage.Output}
		}
		for _, choice := range doc.Choices {
			if choice.Index != 0 {
				continue
			}
			d := choice.Delta
			think := d.Reasoning
			if think == "" {
				think = d.Thinking
			}
			content.WriteString(d.Content)
			reasoning.WriteString(think)
			emit(Delta{Content: d.Content, Reasoning: think})
			for _, fragment := range d.Calls {
				if fragment.Index < 0 || fragment.Index >= 32 {
					return fmt.Errorf("índice de ferramenta inválido")
				}
				call := calls[fragment.Index]
				if call == nil {
					call = &Call{}
					calls[fragment.Index] = call
				}
				if fragment.ID != "" {
					call.ID = fragment.ID
				}
				call.Function.Name += fragment.Function.Name
				call.Function.Arguments += fragment.Function.Arguments
				if len(call.Function.Arguments) > 64000 || len(call.Function.Name) > 200 {
					return fmt.Errorf("chamada de ferramenta muito grande")
				}
			}
			if choice.Finish != "" {
				out.Finish = choice.Finish
				ended = true
			}
		}
		return nil
	})
	out.Content = content.String()
	out.Reasoning = reasoning.String()
	keys := []int{}
	for i := range calls {
		keys = append(keys, i)
	}
	sort.Ints(keys)
	for _, i := range keys {
		out.Calls = append(out.Calls, *calls[i])
	}
	if err != nil {
		return out, err
	}
	if !ended {
		return out, fmt.Errorf("stream interrompido antes da conclusão")
	}
	out.Calls, err = finishCalls(out.Calls)
	return out, err
}

func (c *Client) ollama(ctx context.Context, messages []Message, tools []Tool, emit func(Delta)) (Response, error) {
	converted := []map[string]any{}
	for _, m := range messages {
		x := map[string]any{"role": m.Role, "content": m.Content}
		if m.Role == "tool" {
			x["tool_name"] = m.Name
		}
		if len(m.ToolCalls) > 0 {
			calls := []map[string]any{}
			for _, call := range m.ToolCalls {
				var args any
				if err := json.Unmarshal([]byte(call.Function.Arguments), &args); err != nil {
					return Response{}, err
				}
				calls = append(calls, map[string]any{"id": call.ID, "type": "function", "function": map[string]any{"name": call.Function.Name, "arguments": args}})
			}
			x["tool_calls"] = calls
		}
		converted = append(converted, x)
	}
	body := map[string]any{"model": c.Settings.Model, "messages": converted, "stream": true, "options": map[string]any{"num_ctx": c.Settings.ContextWindow}}
	if len(tools) > 0 {
		body["tools"] = tools
	}
	resp, err := c.request(ctx, "chat", body)
	if err != nil {
		return Response{}, err
	}
	defer resp.Body.Close()
	var out Response
	var content, reasoning strings.Builder
	done := false
	err = scanStream(resp.Body, false, func(data string) error {
		var doc struct {
			Message struct {
				Content  string `json:"content"`
				Thinking string `json:"thinking"`
				Calls    []struct {
					ID       string `json:"id"`
					Function struct {
						Name      string          `json:"name"`
						Arguments json.RawMessage `json:"arguments"`
					} `json:"function"`
				} `json:"tool_calls"`
			} `json:"message"`
			Done   bool   `json:"done"`
			Reason string `json:"done_reason"`
			Input  int    `json:"prompt_eval_count"`
			Output int    `json:"eval_count"`
			Error  string `json:"error"`
		}
		if err := json.Unmarshal([]byte(data), &doc); err != nil {
			return fmt.Errorf("evento Ollama inválido: %w", err)
		}
		if doc.Error != "" {
			return fmt.Errorf("Ollama retornou erro: %s", doc.Error)
		}
		content.WriteString(doc.Message.Content)
		reasoning.WriteString(doc.Message.Thinking)
		emit(Delta{Content: doc.Message.Content, Reasoning: doc.Message.Thinking})
		for _, call := range doc.Message.Calls {
			args := string(call.Function.Arguments)
			if strings.HasPrefix(args, "\"") {
				_ = json.Unmarshal(call.Function.Arguments, &args)
			}
			out.Calls = append(out.Calls, Call{ID: call.ID, Function: Function{Name: call.Function.Name, Arguments: args}})
			if len(out.Calls) > 32 {
				return fmt.Errorf("muitas ferramentas em uma chamada")
			}
		}
		if doc.Done {
			done = true
			out.Finish = doc.Reason
			out.Usage = Usage{doc.Input, doc.Output}
			return io.EOF
		}
		return nil
	})
	out.Content = content.String()
	out.Reasoning = reasoning.String()
	if err != nil {
		return out, err
	}
	if !done {
		return out, fmt.Errorf("stream Ollama interrompido antes da conclusão")
	}
	out.Calls, err = finishCalls(out.Calls)
	return out, err
}

func scanStream(reader io.Reader, sse bool, consume func(string) error) error {
	limited := &io.LimitedReader{R: reader, N: MaxStreamBytes + 1}
	scanner := bufio.NewScanner(limited)
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	var event strings.Builder
	flush := func() error {
		if event.Len() == 0 {
			return nil
		}
		data := strings.TrimSuffix(event.String(), "\n")
		event.Reset()
		return consume(data)
	}
	for scanner.Scan() {
		line := scanner.Text()
		if sse {
			if line == "" {
				if err := flush(); err != nil {
					if errors.Is(err, io.EOF) {
						return nil
					}
					return err
				}
			} else if strings.HasPrefix(line, "data:") {
				event.WriteString(strings.TrimPrefix(strings.TrimPrefix(line, "data:"), " "))
				event.WriteByte('\n')
				if event.Len() > 1024*1024 {
					return fmt.Errorf("evento SSE excede 1 MiB")
				}
			}
		} else if strings.TrimSpace(line) != "" {
			if err := consume(line); err != nil {
				if errors.Is(err, io.EOF) {
					return nil
				}
				return err
			}
		}
	}
	if limited.N <= 0 {
		return fmt.Errorf("stream excede 16 MiB")
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("leitura do stream: %w", err)
	}
	if err := flush(); err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	return nil
}
