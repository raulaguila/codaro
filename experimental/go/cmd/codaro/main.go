package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
	"github.com/raulaguila/codaro/experimental/go/internal/tools"
	"github.com/raulaguila/codaro/experimental/go/internal/ui"
)

const version = "0.1.0-experimental"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "Codaro Go:", err)
		os.Exit(1)
	}
}
func run() error {
	demo := flag.Bool("demo", false, "Demonstração sem API, com leitura real do projeto")
	prompt := flag.String("prompt", "", "Pergunta única, sem abrir a interface")
	showVersion := flag.Bool("version", false, "Mostrar versão")
	flag.Usage = func() {
		fmt.Fprint(flag.CommandLine.Output(), "Codaro Go · protótipo independente\n\nUso: codaro-go [--demo] [--prompt pergunta] [diretório]\n\n")
		flag.PrintDefaults()
	}
	flag.Parse()
	if *showVersion {
		fmt.Println("Codaro Go", version)
		return nil
	}
	if flag.NArg() > 1 {
		return fmt.Errorf("informe somente um diretório")
	}
	path := "."
	if flag.NArg() == 1 {
		path = flag.Arg(0)
	}
	repo, err := tools.Open(path)
	if err != nil {
		return err
	}
	defer repo.Close()
	dir, err := config.Directory()
	if err != nil {
		return err
	}
	settings, err := config.Load(dir)
	if err != nil {
		return err
	}
	var provider llm.Provider
	if *demo {
		settings.Provider = "demo"
		settings.Model = "demo-local"
		provider = llm.Demo{}
	} else {
		provider = llm.New(settings)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if *prompt != "" {
		if settings.Model == "" {
			return fmt.Errorf("configure um modelo pela UI ou CODARO_GO_MODEL")
		}
		if !*demo {
			info, e := provider.Describe(ctx, settings.Model)
			if e == nil {
				settings = llm.ResolveContext(settings, info)
				provider = llm.New(settings)
			} else {
				fmt.Fprintln(os.Stderr, "Metadados indisponíveis; usando janela configurada:", config.Redact(e.Error(), settings.APIKey))
			}
		}
		a := agent.Agent{Repository: repo, Provider: provider, Settings: settings, TracePath: agent.TracePath(dir, repo.Path)}
		_, err = a.Run(ctx, *prompt, nil, func(event agent.Event) {
			switch event.Kind {
			case "tool_start", "phase", "notice":
				fmt.Fprintln(os.Stderr, event.Text)
			case "final":
				fmt.Fprintln(os.Stdout, event.Text)
			}
		})
		return err
	}
	if !*demo && settings.Model != "" && strings.TrimSpace(os.Getenv("CODARO_GO_CONTEXT_WINDOW")) == "" {
		settings.ContextSource = "limite configurado; /models consulta metadados"
	}
	app := ui.New(repo, settings, dir, *demo)
	defer app.Shutdown()
	program := tea.NewProgram(app, tea.WithAltScreen(), tea.WithMouseCellMotion(), tea.WithFPS(30), tea.WithContext(ctx))
	_, err = program.Run()
	return err
}
