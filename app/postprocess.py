"""Lightweight text cleanup applied after ASR (case normalization)."""

import re

_ALL_CAPS_WORD = re.compile(r"\b[A-Z]{2,}\b")
_HAS_LOWER_LETTER = re.compile(r"[a-z]")
_STANDALONE_I = re.compile(r"\bi\b")
_SENTENCE_ENDERS = set("。！？!?；;")
_FULLWIDTH_TO_ASCII = str.maketrans("，。！？：；", ",.!?:;")


def looks_all_caps(text: str) -> bool:
    """True when the text has English words but none in lowercase (SenseVoice style)."""
    return bool(_ALL_CAPS_WORD.search(text)) and not _HAS_LOWER_LETTER.search(text)


def normalize_english_case(text: str) -> str:
    """Convert ALL-CAPS English to sentence case; CJK/Kana/Hangul are untouched."""
    lowered = text.lower()
    out = []
    capitalize_next = True
    for ch in lowered:
        if capitalize_next:
            if ch.isalpha():
                out.append(ch.upper())
                capitalize_next = False
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
    """Map fullwidth punctuation to ASCII, for pure-English transcripts."""
    return text.translate(_FULLWIDTH_TO_ASCII)
