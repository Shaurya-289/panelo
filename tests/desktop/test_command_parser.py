import pytest

from productivity_app.desktop.ui.command_parser import GRAMMAR, duration_ms, help_text, lex, parse

ID = "f3924fe7-798e-4d21-b734-0ae5c8925010"


@pytest.mark.parametrize("pattern", GRAMMAR)
def test_every_registered_grammar_parses_and_is_in_help(pattern):
    text = (
        pattern.replace("<text>", '"plain words"')
        .replace("<uuid>", ID)
        .replace("<duration>", "25m")
        .replace("<panel>", "notes")
        .replace("<family>", "timer")
    )
    assert parse(text).pattern == pattern
    assert pattern.replace("<text>", '"text"') in help_text()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "HELP",
        "new task plain",
        'new task ""',
        'new note "unfinished',
        'search "bad\\n"',
        "help;refresh",
        "help | refresh",
        "help > file",
        "help # comment",
        "$(help)",
        "timer new 0m",
        "timer new 1.5m",
        "timer new -1m",
        "timer new 20",
        "timer pause extra",
        "open folder wrong",
        "layout swap Notes todo",
        "focus break medium",
        "focus next auto",
        "panel maximize focus",
        "new note extra extra",
        "timer start " + ID + " extra",
    ],
)
def test_security_and_exact_argument_rejection(text):
    with pytest.raises(ValueError):
        parse(text)


def test_quoted_literals_and_integer_duration():
    assert lex(r'search "say \"yes\" \\ literal;|#"')[1].value == 'say "yes" \\ literal;|#'
    assert duration_ms("3h") == 10800000
    assert duration_ms("1ms") == 1
    assert duration_ms("2s") == 2000
    assert duration_ms("25m") == 1500000
