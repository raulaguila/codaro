"""The approved terminal palette, with semantic colors retained for alternate themes."""

from textual.theme import Theme

CODARO_THEME = Theme(
    name="codaro",
    primary="#568da0",
    secondary="#94a3b6",
    accent="#83d6d7",
    foreground="#dfe6ef",
    background="#0f1723",
    surface="#17212e",
    panel="#17212e",
    success="#89c8a2",
    warning="#e5bd78",
    error="#e5969b",
    dark=True,
)
