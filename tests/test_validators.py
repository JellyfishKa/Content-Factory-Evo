"""Tests for validators.py. Pure Python, no LLM/DB, offline-green.

Two cases per rule: one passes, one fails.
"""
import validators
from schemas import Final

CFG = {
    "style": {
        "blacklist": ["всем привет", "сегодня расскажу", "давайте", "надеюсь, было полезно", "подписывайтесь"],
        "require_number_in_first_screen": True,
        "hashtags_as_single_block": True,
        "no_caps_for_claude": True,
    },
    "limits": {
        "article_chars": [800, 1100],
        "post_words": [100, 300],
    },
}


def _article_body(n_chars: int) -> str:
    filler = "В 2024 году компания выпустила новую модель и контекст вырос до 200к токенов. "
    body = (filler * (n_chars // len(filler) + 1))[:n_chars]
    return body


def _post_body(n_words: int) -> str:
    return " ".join(["слово1"] * n_words)


# check_blacklist

def test_check_blacklist_pass():
    result = validators.check_blacklist("Обычный текст без проблем.", CFG)
    assert result.passed is True


def test_check_blacklist_fail():
    result = validators.check_blacklist("Всем привет, сегодня расскажу о новинке.", CFG)
    assert result.passed is False
    assert "всем привет" in result.detail.lower()


# check_number_in_first_screen

def test_check_number_in_first_screen_pass():
    text = "В 2024 году вышла новая модель." + "текст " * 200
    result = validators.check_number_in_first_screen(text, CFG)
    assert result.passed is True


def test_check_number_in_first_screen_fail():
    text = "Без цифр тут совсем. " * 100
    result = validators.check_number_in_first_screen(text, CFG)
    assert result.passed is False


# check_no_signoff

def test_check_no_signoff_pass():
    result = validators.check_no_signoff("Обычный текст без прощаний.")
    assert result.passed is True


def test_check_no_signoff_fail():
    result = validators.check_no_signoff("Вот и всё, надеюсь, было полезно!")
    assert result.passed is False


# check_hashtags_single_block

def test_check_hashtags_single_block_pass():
    text = "Основной текст без хештегов внутри.\n\n#тема1 #тема2"
    result = validators.check_hashtags_single_block(text)
    assert result.passed is True


def test_check_hashtags_single_block_fail():
    text = "Текст #тема1 в середине.\nОстальной текст.\n#тема2 в конце."
    result = validators.check_hashtags_single_block(text)
    assert result.passed is False


# check_no_caps_for_claude

def test_check_no_caps_for_claude_pass():
    result = validators.check_no_caps_for_claude("Обычный текст без капса.", CFG)
    assert result.passed is True


def test_check_no_caps_for_claude_fail():
    result = validators.check_no_caps_for_claude("ЭТО КРИТИЧЕСКИ ВАЖНО запомнить.", CFG)
    assert result.passed is False


# check_length

def test_check_length_article_pass():
    result = validators.check_length(_article_body(900), "article", CFG)
    assert result.passed is True


def test_check_length_article_fail():
    result = validators.check_length(_article_body(100), "article", CFG)
    assert result.passed is False


def test_check_length_post_pass():
    result = validators.check_length(_post_body(150), "post", CFG)
    assert result.passed is True


def test_check_length_post_fail():
    result = validators.check_length(_post_body(10), "post", CFG)
    assert result.passed is False


# run_all

def test_run_all_pass_article():
    final = Final(kind="article", title="t", body=_article_body(900), style_passed=True)
    results = validators.run_all(final, CFG)
    assert all(r.passed for r in results)
    assert len(results) == 5  # no hashtag check for articles


def test_run_all_fail_post():
    final = Final(
        kind="post",
        title="t",
        body="Всем привет! " + _post_body(150),
        style_passed=True,
    )
    results = validators.run_all(final, CFG)
    assert len(results) == 6  # hashtag check applies to posts
    failed_rules = {r.rule for r in results if not r.passed}
    assert "check_blacklist" in failed_rules
