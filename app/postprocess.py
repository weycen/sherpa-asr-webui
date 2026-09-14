"""Lightweight text cleanup applied after ASR (case normalization)."""

import re

_ALL_CAPS_WORD = re.compile(r"\b[A-Z]{2,}\b")
_HAS_LOWER_LETTER = re.compile(r"[a-z]")
_STANDALONE_I = re.compile(r"\bi\b")
_SENTENCE_ENDERS = set("。！？!?；;.")
_FULLWIDTH_TO_ASCII = str.maketrans("，。！？：；", ",.!?:;")
_CJK_CHAR = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")


def looks_all_caps(text: str) -> bool:
    """True when the text has English words but none in lowercase (SenseVoice style).

    Only applies to transcripts where Latin words are the dominant language
    to avoid downcasing abbreviations/brand names in CJK sentences (e.g. CPU, APPLE).
    """
    if not _ALL_CAPS_WORD.search(text) or _HAS_LOWER_LETTER.search(text):
        return False
    cjk_count = len(_CJK_CHAR.findall(text))
    latin_words = len(_LATIN_WORD.findall(text))
    if cjk_count > 0 and cjk_count >= latin_words:
        return False
    return True


def normalize_english_case(text: str) -> str:
    """Convert ALL-CAPS English to sentence case; CJK/Kana/Hangul are untouched."""
    lowered = text.lower()
    out = []
    capitalize_next = True
    for ch in lowered:
        if capitalize_next:
            if "a" <= ch <= "z":
                out.append(ch.upper())
                capitalize_next = False
            elif ch.isalpha():
                # Non-Latin letter (e.g. CJK): preserve it without consuming capitalize_next
                out.append(ch)
            elif not (ch.isspace() or ch in "\"'“”‘’([{<"):
                capitalize_next = False
            else:
                out.append(ch)
        else:
            out.append(ch)
            if ch in _SENTENCE_ENDERS:
                capitalize_next = True
    return _STANDALONE_I.sub("I", "".join(out))


def ascii_punctuation_for_english(text: str) -> str:
    """Map fullwidth punctuation to ASCII with proper spacing, for pure-English transcripts."""
    converted = text.translate(_FULLWIDTH_TO_ASCII)
    # Add a space after ASCII punctuation if followed by an ASCII character/digit
    spaced = re.sub(r"([,!?:;])(?=[A-Za-z0-9])", r"\1 ", converted)
    return re.sub(r"(\.)(?=[A-Za-z])", r"\1 ", spaced)


def text_stats(text: str) -> dict:
    """Count transcript size: CJK characters + one per Latin word (字)."""
    return {"char_count": len(_CJK_CHAR.findall(text)) + len(_LATIN_WORD.findall(text))}
