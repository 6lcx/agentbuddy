"""Approximate preflight token budgeting, not an exact provider tokenizer.

UTF-8 bytes are not tokens. Use a deliberately conservative text estimate plus
the caller's 2048-token reserve; the provider remains authoritative at its limit.
"""

import math
import re


def estimate_tokens(text):
    # Split ASCII word/number runs from punctuation; non-ASCII gets two tokens
    # per code point, ASCII runs at least one token per three characters.
    return sum(
        math.ceil(len(part) / 3)
        if part.isascii() and part.isalnum()
        else len(part) * (1 if part.isascii() else 2)
        for part in re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]", text)
    )
