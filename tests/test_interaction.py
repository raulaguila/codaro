import pytest

from codaro.interaction import InputHistory, completions, references


def test_history_restores_draft_and_new_typing_resets_navigation():
    history = InputHistory(["um", "dois"])
    assert history.previous("rascunho") == "dois"
    assert history.previous("dois") == "um"
    assert history.next("um") == "dois"
    assert history.next("dois") == "rascunho"
    assert history.previous("novo") == "dois"
    assert history.previous("alterado") == "dois"
    assert history.next("dois") == "alterado"


def test_completions_preserve_suffix_and_quote_paths_with_spaces():
    text = "Leia @src/m e explique"
    options = completions(text, len("Leia @src/m"), ["src/main.py", "src/my file.py"])
    assert text[: options[0].start] + options[0].value + text[options[0].end :] == (
        "Leia @src/main.py  e explique"
    )
    assert options[1].value == '@"src/my file.py" '
    assert completions("/sta", 4, [])[0].value == "/status "
    assert completions("user@host", 9, ["host.py"]) == []


def test_reference_parsing_deduplicates_and_limits_paths():
    assert references('Leia @src/main.py e @"pasta com espaços/a.py" @src/main.py') == [
        "src/main.py",
        "pasta com espaços/a.py",
    ]
    assert references("mail@example.com") == []
    with pytest.raises(ValueError, match="quatro"):
        references("@a.py @b.py @c.py @d.py @e.py")


def test_reference_completion_roundtrips_quotes_and_backslashes():
    path = 'folder/a"b\\c.py'
    option = completions("@folder/", 8, [path])[0]
    assert references("Leia " + option.value) == [path]
