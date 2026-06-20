"""STYLE checks as pure functions. No LLM calls here.

Each check_* returns a CheckResult(rule, passed, detail). run_all collects
all checks that apply to the given Final (article checks differ from post
checks only in check_length and check_hashtags_single_block).

This is the anti-overfitting gate: a new STYLE rule means a new function
here plus tests, never a new sentence in a prompt.
"""
from __future__ import annotations

import re

from schemas import CheckResult, Final

SIGNOFF_PHRASES = ["надеюсь, было полезно", "подписывайтесь"]
CAPS_PHRASE = "критически важно"


def check_blacklist(text: str, cfg: dict) -> CheckResult:
    """No phrase from style.blacklist appears in text."""
    blacklist = cfg.get("style", {}).get("blacklist", [])
    lowered = text.lower()
    hits = [phrase for phrase in blacklist if phrase.lower() in lowered]
    if hits:
        return CheckResult(
            rule="check_blacklist",
            passed=False,
            detail=f"Найдены запрещённые фразы: {', '.join(hits)}",
        )
    return CheckResult(rule="check_blacklist", passed=True, detail="")


def check_number_in_first_screen(text: str, cfg: dict) -> CheckResult:
    """A digit appears within the first ~600 chars, if the rule is enabled."""
    if not cfg.get("style", {}).get("require_number_in_first_screen", True):
        return CheckResult(rule="check_number_in_first_screen", passed=True, detail="")
    first_screen = text[:600]
    if re.search(r"\d", first_screen):
        return CheckResult(rule="check_number_in_first_screen", passed=True, detail="")
    return CheckResult(
        rule="check_number_in_first_screen",
        passed=False,
        detail="В первых ~600 символах нет ни одной цифры",
    )


def check_no_signoff(text: str) -> CheckResult:
    """No sign-off phrases like 'надеюсь, было полезно' / 'подписывайтесь'."""
    lowered = text.lower()
    hits = [phrase for phrase in SIGNOFF_PHRASES if phrase in lowered]
    if hits:
        return CheckResult(
            rule="check_no_signoff",
            passed=False,
            detail=f"Найдена фраза прощания: {', '.join(hits)}",
        )
    return CheckResult(rule="check_no_signoff", passed=True, detail="")


def check_hashtags_single_block(text: str) -> CheckResult:
    """If hashtags are present, they all sit in one block at the end."""
    lines = text.strip().splitlines()
    hashtag_line_idxs = [i for i, line in enumerate(lines) if "#" in line]
    if not hashtag_line_idxs:
        return CheckResult(rule="check_hashtags_single_block", passed=True, detail="")

    last_idx = len(lines) - 1
    block_start = hashtag_line_idxs[0]
    contiguous = hashtag_line_idxs == list(range(block_start, block_start + len(hashtag_line_idxs)))
    at_end = hashtag_line_idxs[-1] == last_idx

    if contiguous and at_end:
        return CheckResult(rule="check_hashtags_single_block", passed=True, detail="")
    return CheckResult(
        rule="check_hashtags_single_block",
        passed=False,
        detail="Хештеги разбросаны по тексту, а не собраны одним блоком в конце",
    )


def check_no_caps_for_claude(text: str, cfg: dict) -> CheckResult:
    """No ALLCAPS words / 'КРИТИЧЕСКИ ВАЖНО' when the model in use is Claude."""
    if not cfg.get("style", {}).get("no_caps_for_claude", True):
        return CheckResult(rule="check_no_caps_for_claude", passed=True, detail="")

    if CAPS_PHRASE in text.lower():
        return CheckResult(
            rule="check_no_caps_for_claude",
            passed=False,
            detail="Найдена фраза 'КРИТИЧЕСКИ ВАЖНО'",
        )

    caps_words = re.findall(r"\b[А-ЯA-Z]{4,}\b", text)
    if caps_words:
        return CheckResult(
            rule="check_no_caps_for_claude",
            passed=False,
            detail=f"Найден текст КАПСОМ: {', '.join(caps_words[:5])}",
        )
    return CheckResult(rule="check_no_caps_for_claude", passed=True, detail="")


def check_length(text: str, kind: str, cfg: dict) -> CheckResult:
    """Length is within the corridor from cfg.limits (article_chars / post_words)."""
    limits = cfg.get("limits", {})
    if kind == "article":
        lo, hi = limits.get("article_chars", [0, 10**9])
        length = len(text)
        unit = "символов"
    else:
        lo, hi = limits.get("post_words", [0, 10**9])
        length = len(text.split())
        unit = "слов"

    if lo <= length <= hi:
        return CheckResult(rule="check_length", passed=True, detail="")
    return CheckResult(
        rule="check_length",
        passed=False,
        detail=f"Длина {length} {unit} вне коридора [{lo}, {hi}]",
    )


def run_all(final: Final, cfg: dict) -> list[CheckResult]:
    """Runs all checks applicable to final.kind (hashtags only matter for posts)."""
    text = final.body
    results = [
        check_blacklist(text, cfg),
        check_number_in_first_screen(text, cfg),
        check_no_signoff(text),
        check_no_caps_for_claude(text, cfg),
        check_length(text, final.kind, cfg),
    ]
    if final.kind == "post":
        results.append(check_hashtags_single_block(text))
    return results
