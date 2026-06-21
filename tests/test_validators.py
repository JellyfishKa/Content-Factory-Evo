"""Tests for validators.py. Pure Python, no LLM/DB, offline-green.

По два теста на каждое правило: позитивный (проходит) и негативный (падает).
"""
import validators
from src.schemas import Final

CFG = {
    "style": {
        "blacklist": ["всем привет", "сегодня расскажу", "давайте", "уникальный"],
        "require_number_in_first_screen": True,
        "no_caps_for_claude": True,
    },
    "limits": {
        "article_chars": [800, 1100],
        "post_words": [100, 300],
    },
}


def _article_body(n_chars: int) -> str:
    """Генератор текста заданной длины с цифрой в начале."""
    filler = "В 2024 году компания выпустила новую модель. "
    body = (filler * (n_chars // len(filler) + 1))[:n_chars]
    return body


def _post_body(n_words: int) -> str:
    """Генератор текста из N слов с цифрой в начале."""
    return "В 2024 году " + " ".join(["слово"] * (n_words - 3))


def test_check_blacklist_pass():
    result = validators.check_blacklist("Это обычный текст о технологиях.", CFG)
    assert result.passed is True


def test_check_blacklist_fail():
    result = validators.check_blacklist("Всем привет, сегодня я расскажу про уникальный метод.", CFG)
    assert result.passed is False
    assert "всем привет" in result.detail.lower()
    assert "уникальный" in result.detail.lower()


def test_check_number_in_first_screen_pass():
    text = "В 2024 году нейросети стали стандартом." + "а" * 700
    result = validators.check_number_in_first_screen(text, CFG)
    assert result.passed is True


def test_check_number_in_first_screen_fail():
    text = "Тут только буквы и знаки препинания. " * 20
    result = validators.check_number_in_first_screen(text, CFG)
    assert result.passed is False
    assert "нет ни одной цифры" in result.detail or "600" in result.detail


def test_check_no_signoff_pass():
    result = validators.check_no_signoff("Текст заканчивается выводом по делу.")
    assert result.passed is True


def test_check_no_signoff_fail():
    result = validators.check_no_signoff("Надеюсь, было полезно! Подписывайтесь на канал.")
    assert result.passed is False
    assert "надеюсь, было полезно" in result.detail.lower()


def test_check_hashtags_single_block_pass():
    text = "Это текст поста.\n\n#ии #нейросети #гайд"
    result = validators.check_hashtags_single_block(text)
    assert result.passed is True


def test_check_hashtags_single_block_fail():
    text = "Посмотрите на этот #ии-инструмент. Он работает круто.\n#теги"
    result = validators.check_hashtags_single_block(text)
    assert result.passed is False
    assert "блоком в конце" in result.detail


def test_check_no_caps_for_claude_pass():
    result = validators.check_no_caps_for_claude("Это нормальный Текст с аббревиатурой ИИ.", CFG)
    assert result.passed is True

def test_check_no_caps_for_claude_fail():
    result = validators.check_no_caps_for_claude("Это КРИТИЧЕСКИ ВАЖНО и ОЧЕНЬ ГРОМКО.", CFG)
    assert result.passed is False
    assert "КАПСОМ" in result.detail or "КРИТИЧЕСКИ ВАЖНО" in result.detail


def test_check_length_article_pass():
    result = validators.check_length(_article_body(900), "article", CFG)
    assert result.passed is True

def test_check_length_article_fail():
    result = validators.check_length(_article_body(100), "article", CFG)
    assert result.passed is False
    assert "вне коридора" in result.detail or "диапазона" in result.detail


def test_check_length_post_pass():
    result = validators.check_length(_post_body(150), "post", CFG)
    assert result.passed is True


def test_check_length_post_fail():
    result = validators.check_length(_post_body(10), "post", CFG)
    assert result.passed is False


def test_run_all_pass_article():
    final = Final(
        kind="article",
        title="Тестовая статья",
        body=_article_body(900),
        style_passed=True
    )
    results = validators.run_all(final, CFG)
    assert all(r.passed for r in results)
    assert len(results) == 5


def test_run_all_fail_post():
    final = Final(
        kind="post",
        title="Тестовый пост",
        body="Всем привет! " + _post_body(150),
        style_passed=True,
    )
    results = validators.run_all(final, CFG)
    assert len(results) == 6
    failed_rules = {r.rule for r in results if not r.passed}
    assert "check_blacklist" in failed_rules


# check_max_sentence_length

def test_check_max_sentence_length_pass():
    text = "Это короткое предложение. Вот ещё одно, тоже короткое."
    result = validators.check_max_sentence_length(text, CFG)
    assert result.passed is True


def test_check_max_sentence_length_fail():
    text = "слово " * 50 + "."
    result = validators.check_max_sentence_length(text, CFG)
    assert result.passed is False


# check_no_repeated_words

def test_check_no_repeated_words_pass():
    result = validators.check_no_repeated_words("Это очень хороший текст без повторов.", CFG)
    assert result.passed is True


def test_check_no_repeated_words_fail():
    result = validators.check_no_repeated_words("Это очень очень хороший текст.", CFG)
    assert result.passed is False
    assert "очень" in result.detail.lower()
