"""The approved terminal palette, with semantic colors retained for alternate themes."""

from textual.theme import Theme

CODARO_THEME = Theme(
    name="codaro",
    primary="#568da0",
    secondary="#94a3b6",
    accent="#83d6d7",
    foreground="#dfe6ef",
    background="#111821",
    surface="#17212d",
    panel="#17212d",
    success="#89c8a2",
    warning="#e5bd78",
    error="#e5969b",
    dark=True,
    variables={
        "text-muted": "#94a3b6",
        "text-disabled": "#8392a6",
        "button-focus-text-style": "none",
    },
)
