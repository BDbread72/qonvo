"""
i18n 모듈
TOML 기반 다국어 지원. t("section.key") 로 번역 문자열 반환.
"""
import sys
import tomllib
from pathlib import Path


def _base_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / "lang"
    return Path(__file__).resolve().parent.parent.parent / "lang"


_strings: dict = {}
_lang: str = "KR"


def _flatten(data: dict, prefix: str = "") -> dict:
    """중첩 딕셔너리를 "section.key" 형태로 평탄화"""
    result = {}
    for k, v in data.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten(v, full_key))
        else:
            result[full_key] = str(v)
    return result


def load(lang: str = None):
    """언어 파일 로드"""
    global _strings, _lang

    if lang:
        _lang = lang

    toml_path = _base_path() / f"{_lang}.toml"
    if not toml_path.exists():
        # 폴백: KR
        toml_path = _base_path() / "KR.toml"

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    _strings = _flatten(raw)


def t(key: str, **kwargs) -> str:
    """
    번역 문자열 반환.
    t("menu.file") → "파일(&F)"
    t("error.api_error", error="timeout") → "[오류] timeout"
    """
    if not _strings:
        load()

    text = _strings.get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text
