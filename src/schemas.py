"""Pydantic contracts between pipeline stages.

The shared schema every stage reads and writes — input/output stays typed
so stages can't drift apart.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Source(BaseModel):
    """Input source fed into the planner.

    Example:
        Source(
            type="transcript",
            ref="inputs/transcript.txt",
            text="Сегодня поговорим о том, как устроена наша новая модель...",
        )
    """

    type: Literal["transcript", "article", "topic"]
    ref: str
    text: str


class Scenario(BaseModel):
    """One planner output item: a slice of the source to turn into content.

    Example:
        Scenario(
            idx=1,
            topic="Запуск новой модели",
            angle="Что изменилось для разработчиков",
            brief_hint="Сфокусироваться на API-изменениях и миграции",
        )
    """

    idx: int
    topic: str
    angle: str
    brief_hint: str


class Brief(BaseModel):
    """Researcher output: facts gathered for one scenario.

    Example:
        Brief(
            facts=["Модель поддерживает контекст 200k токенов"],
            confirmed=["Релиз состоялся 15 июня"],
            not_confirmed=["Точная дата прекращения поддержки старой версии"],
            quotes=["\"Это самый быстрый релиз за всю историю компании\" - CEO"],
        )
    """

    facts: list[str] = Field(default_factory=list)
    confirmed: list[str] = Field(default_factory=list)
    not_confirmed: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    """Writer output: a draft article or post before editing.

    Example:
        Draft(
            kind="article",
            title="Новая модель: что нужно знать разработчикам",
            body="Компания выпустила новую модель 15 июня. Контекст 200k токенов...",
        )
    """

    kind: Literal["article", "post"]
    title: str
    body: str


class Final(BaseModel):
    """Editor output: a finalized draft after style checks.

    Example:
        Final(
            kind="article",
            title="Новая модель: что нужно знать разработчикам",
            body="Компания выпустила новую модель 15 июня. Контекст 200k токенов...",
            style_passed=True,
        )
    """

    kind: Literal["article", "post"]
    title: str
    body: str
    style_passed: bool


class Meta(BaseModel):
    """SEO output: metadata for a finalized piece.

    Example:
        Meta(
            title="Новая модель: что нужно знать разработчикам",
            description="Разбираем главные изменения релиза 15 июня для разработчиков",
            keywords=["новая модель", "API", "миграция"],
            slug="novaya-model-chto-nuzhno-znat",
            og_title="Новая модель: гайд для разработчиков",
            og_description="Главные изменения релиза 15 июня одним текстом",
            tags=["релизы", "API"],
        )
    """

    title: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    slug: str
    og_title: str
    og_description: str
    tags: list[str] = Field(default_factory=list)


class CheckResult(BaseModel):
    """Result of a single STYLE validator rule.

    Example:
        CheckResult(
            rule="check_blacklist",
            passed=False,
            detail="Найдена запрещённая фраза: 'подписывайтесь'",
        )
    """

    rule: str
    passed: bool
    detail: str = ""
