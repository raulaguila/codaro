"""Directory shortcuts and concise help, retaining all existing CLI commands."""

from pathlib import Path

import typer
from typer.core import TyperGroup

COMMON_COMMANDS = {"chat", "ask", "plan", "execute", "config", "providers", "models", "doctor"}


class CodaroGroup(TyperGroup):
    def collect_usage_pieces(self, ctx):
        return ["[DIRETÓRIO]", "[OPÇÕES]", "ou COMANDO [ARGS]..."]

    def get_params(self, ctx):
        params = super().get_params(ctx)
        for param in params:
            if param.name in {"install_completion", "show_completion"}:
                param.hidden = not ctx.meta.get("help_all", False)
        return params

    def list_commands(self, ctx):
        names = super().list_commands(ctx)
        return (
            names
            if ctx.meta.get("help_all") or ctx.resilient_parsing
            else [name for name in names if name in COMMON_COMMANDS]
        )

    def resolve_command(self, ctx, args):
        if args:
            target = args[0]
            explicit_path = (
                target in {".", ".."} or "/" in target or "\\" in target or target.startswith("~")
            )
            if target not in self.commands and (
                explicit_path or Path(target).expanduser().is_dir()
            ):
                return super().resolve_command(ctx, ["chat", "--repo", target, *args[1:]])
        return super().resolve_command(ctx, args)


def help_all(ctx: typer.Context, value: bool):
    if value and not ctx.resilient_parsing:
        ctx.meta["help_all"] = True
        for param in ctx.command.params:
            if param.name in {"install_completion", "show_completion"}:
                param.hidden = False
        typer.echo(ctx.get_help())
        ctx.exit()
