"""STYLE checks as pure functions. No LLM calls here.

Каждая функция проверяет одно конкретное правило стиля и возвращает CheckResult.
Это позволяет легко масштабировать правила, не раздувая промпты.
"""
from __future__ import annotations

import re
from schemas import CheckResult, Final

SIGNOFF_PHRASES = [
    "надеюсь, было полезно",
    "подписывайтесь",
    "сегодня я расскажу",
    "давайте разберемся",
    "в этой статье мы"
]
CAPS_PHRASE = "критически важно"


def check_blacklist(text: str, cfg: dict) -> CheckResult:
    """Проверка на отсутствие фраз из черного списка в конфиге."""
    blacklist = cfg.get("style", {}).get("blacklist", [])
    lowered = text.lower()
    hits = [phrase for phrase in blacklist if phrase.lower() in lowered]

    if hits:
        return CheckResult(
            rule="check_blacklist",
            passed=False,
            detail=f"Найдены запрещённые фразы: {', '.join(hits)}",
        )
    return CheckResult(rule="check_blacklist", passed=True, detail="OK")


def check_number_in_first_screen(text: str, cfg: dict) -> CheckResult:
    """В первых ~600 символах должна быть хотя бы одна цифра."""
    if not cfg.get("style", {}).get("require_number_in_first_screen", True):
        return CheckResult(rule="check_number_in_first_screen", passed=True, detail="Проверка отключена")

    first_screen = text[:600]
    if re.search(r"\d", first_screen):
        return CheckResult(rule="check_number_in_first_screen", passed=True, detail="OK")

    return CheckResult(
        rule="check_number_in_first_screen",
        passed=False,
        detail="В первом экране (600 симв.) должна быть цифра для подтверждения экспертности/фактов",
    )


def check_no_signoff(text: str) -> CheckResult:
    """Запрет на стандартные 'блогерские' прощания и вступления."""
    lowered = text.lower()
    hits = [phrase for phrase in SIGNOFF_PHRASES if phrase in lowered]

    if hits:
        return CheckResult(
            rule="check_no_signoff",
            passed=False,
            detail=f"Найдена запрещенная клише-фраза: {', '.join(hits)}",
        )
    return CheckResult(rule="check_no_signoff", passed=True, detail="OK")


def check_hashtags_single_block(text: str) -> CheckResult:
    """Хештеги (если есть) должны идти единым блоком в самом конце текста."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    hashtag_line_idxs = [i for i, line in enumerate(lines) if line.startswith("#")]

    if not hashtag_line_idxs:
        return CheckResult(rule="check_hashtags_single_block", passed=True, detail="Хештегов нет")

    num_hashtags = len(hashtag_line_idxs)
    last_idx = len(lines) - 1

    is_contiguous = hashtag_line_idxs == list(range(hashtag_line_idxs[0], hashtag_line_idxs[0] + num_hashtags))
    is_at_end = hashtag_line_idxs[-1] == last_idx

    if is_contiguous and is_at_end:
        return CheckResult(rule="check_hashtags_single_block", passed=True, detail="OK")

    return CheckResult(
        rule="check_hashtags_single_block",
        passed=False,
        detail="Хештеги должны быть собраны в один блок в самом конце текста",
    )


def check_no_caps_for_claude(text: str, cfg: dict) -> CheckResult:
    """Запрет на слова капсом (длиннее 3 букв) и фразу 'КРИТИЧЕСКИ ВАЖНО'."""
    if not cfg.get("style", {}).get("no_caps_for_claude", True):
        return CheckResult(rule="check_no_caps_for_claude", passed=True, detail="Проверка отключена")

    if CAPS_PHRASE in text.lower():
        return CheckResult(
            rule="check_no_caps_for_claude",
            passed=False,
            detail="Найдена запрещенная фраза 'КРИТИЧЕСКИ ВАЖНО'",
        )

    caps_words = re.findall(r"\b[А-ЯA-Z]{4,}\b", text)
    if caps_words:
        return CheckResult(
            rule="check_no_caps_for_claude",
            passed=False,
            detail=f"Найден текст КАПСОМ: {', '.join(caps_words[:5])}",
        )
    return CheckResult(rule="check_no_caps_for_claude", passed=True, detail="OK")


def check_length(text: str, kind: str, cfg: dict) -> CheckResult:
    """Проверка длины текста согласно лимитам в конфиге."""
    limits = cfg.get("limits", {})
    if kind == "article":
        lo, hi = limits.get("article_chars", [800, 4000])
        val = len(text)
        unit = "симв."
    else:
        lo, hi = limits.get("post_words", [30, 300])
        val = len(text.split())
        unit = "слов"

    if lo <= val <= hi:
        return CheckResult(rule="check_length", passed=True, detail=f"Длина: {val} {unit}")

    return CheckResult(
        rule="check_length",
        passed=False,
        detail=f"Длина {val} {unit} вне диапазона [{lo}-{hi}]",
    )


def check_max_sentence_length(text: str, cfg: dict) -> CheckResult:
    """No sentence longer than style.max_sentence_words words (default 40)."""
    max_words = cfg.get("style", {}).get("max_sentence_words", 40)
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    too_long = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) > max_words:
            too_long.append(f"{len(words)} слов: {sentence[:60]}...")
    if too_long:
        return CheckResult(
            rule="check_max_sentence_length",
            passed=False,
            detail=f"Найдены предложения длиннее {max_words} слов: " + "; ".join(too_long[:3]),
        )
    return CheckResult(rule="check_max_sentence_length", passed=True, detail="")


def check_no_repeated_words(text: str, cfg: dict) -> CheckResult:
    """No immediate duplicated word like 'очень очень'."""
    pattern = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE | re.UNICODE)
    hits = pattern.findall(text)
    if hits:
        return CheckResult(
            rule="check_no_repeated_words",
            passed=False,
            detail=f"Найдены повторённые подряд слова: {', '.join(sorted(set(hits)))}",
        )
    return CheckResult(rule="check_no_repeated_words", passed=True, detail="")


def run_all(final: Final, cfg: dict) -> list[CheckResult]:
    """Запуск всех проверок для финального текста."""
    text = final.body
    results = [
        check_blacklist(text, cfg),
        check_number_in_first_screen(text, cfg),
        check_no_signoff(text),
        check_no_caps_for_claude(text, cfg),
        check_length(text, final.kind, cfg),
        check_max_sentence_length(text, cfg),
        check_no_repeated_words(text, cfg),
    ]
    if final.kind == "post":
        results.append(check_hashtags_single_block(text))

    return results
