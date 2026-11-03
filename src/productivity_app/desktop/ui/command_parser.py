"""Exact application grammar; user input never becomes executable code."""

import re
from dataclasses import dataclass
from uuid import UUID

from productivity_app.desktop.ui.workspace_state import PANEL_IDS


@dataclass(frozen=True, slots=True)
class Token:
    value: str
    quoted: bool = False


@dataclass(frozen=True, slots=True)
class Command:
    family: str
    pattern: str
    arguments: tuple[str, ...]


GRAMMAR = (
    "help",
    "help <family>",
    "new note",
    "new note <text>",
    "new task <text>",
    "open note <uuid>",
    "open task <uuid>",
    "open folder <uuid>",
    "open timer <uuid>",
    "search <text>",
    "search like <text>",
    "timer new <duration>",
    "timer start <uuid>",
    "timer pause",
    "timer resume",
    "timer finish",
    "timer cancel",
    "timer reset",
    "timer reconcile",
    "focus new",
    "focus new task <uuid>",
    "focus break short",
    "focus break long",
    "focus sequence new",
    "focus sequence new task <uuid>",
    "focus next",
    "focus cancel",
    "reminders upcoming",
    "reminders due",
    "reminders overdue",
    "layout edit",
    "layout done",
    "layout reset",
    "layout swap <panel> <panel>",
    "panel maximize <panel>",
    "panel restore",
    "refresh",
)
FAMILIES = tuple(dict.fromkeys(p.split()[0] for p in GRAMMAR))
KEYWORDS = tuple(
    sorted(
        set(PANEL_IDS)
        | {word for pattern in GRAMMAR for word in pattern.split() if not word.startswith("<")}
    )
)


def lex(text: str) -> tuple[Token, ...]:
    if len(text) > 4096:
        raise ValueError("Command too long")
    tokens: list[Token] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        quoted = text[index] == '"'
        value = ""
        if quoted:
            index += 1
            while index < len(text) and text[index] != '"':
                character = text[index]
                if character == "\\":
                    index += 1
                    if index >= len(text) or text[index] not in {'"', "\\"}:
                        raise ValueError('Only \\" and \\\\ are supported inside quotes')
                    character = text[index]
                value += character
                index += 1
            if index >= len(text):
                raise ValueError("Unterminated quote")
            index += 1
            if index < len(text) and not text[index].isspace():
                raise ValueError("Separate arguments with whitespace")
        else:
            while index < len(text) and not text[index].isspace():
                if text[index] in '";|&<>`$#\\':
                    raise ValueError("Chaining, shell syntax and comments are not supported")
                value += text[index]
                index += 1
        tokens.append(Token(value, quoted))
    return tuple(tokens)


def duration_ms(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]{0,17})(ms|s|m|h)", value)
    if match is None:
        raise ValueError("Duration must be a positive integer followed by ms, s, m or h")
    return int(match[1]) * {"ms": 1, "s": 1000, "m": 60000, "h": 3600000}[match[2]]


def parse(text: str) -> Command:
    tokens = lex(text)
    for pattern in GRAMMAR:
        expected = pattern.split()
        if len(expected) != len(tokens):
            continue
        arguments: list[str] = []
        valid = True
        for word, token in zip(expected, tokens, strict=True):
            if word == "<text>":
                valid = token.quoted and bool(token.value.strip())
            elif word == "<uuid>":
                try:
                    valid = not token.quoted and str(UUID(token.value)) == token.value.lower()
                except ValueError:
                    valid = False
            elif word == "<duration>":
                try:
                    duration_ms(token.value)
                    valid = not token.quoted
                except ValueError:
                    valid = False
            elif word == "<panel>":
                valid = not token.quoted and token.value in PANEL_IDS
            elif word == "<family>":
                valid = not token.quoted and token.value in FAMILIES
            else:
                valid = not token.quoted and token.value == word
            if not valid:
                break
            if word.startswith("<"):
                arguments.append(token.value)
        if valid:
            return Command(expected[0], pattern, tuple(arguments))
    raise ValueError("Unsupported command or arguments. Use help.")


def help_text(family: str | None = None) -> str:
    return "\n".join(
        pattern.replace("<text>", '"text"')
        for pattern in GRAMMAR
        if family is None or pattern.split()[0] == family
    )
