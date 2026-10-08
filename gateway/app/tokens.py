"""Token counting when the upstream does not report `usage` (the KServe HF backend, ADR-006).

Uses the model's own tokenizer (tokenizers library, `tokenizer.json`). Output tokens are
counted exactly from the generated text. Input tokens approximate the chat template:
Qwen2.5 wraps each message as `<|im_start|>{role}\\n{content}<|im_end|>\\n` (content + 5 tokens)
and appends `<|im_start|>assistant\\n` (3 tokens). If no tokenizer is available the gateway
falls back to a characters/4 estimate and says so in `llm_slo.token_source`.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

SOURCE_UPSTREAM = "upstream"
SOURCE_TOKENIZER = "tokenizer"
SOURCE_ESTIMATE = "estimate"

_PER_MESSAGE_OVERHEAD = 5
_GENERATION_PROMPT_OVERHEAD = 3


def _text_of(content) -> str:
    if isinstance(content, list):  # OpenAI content parts
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


class TokenCounter:
    def __init__(self, tokenizer_path: str | None = None):
        self._tok = None
        if tokenizer_path and Path(tokenizer_path).is_file():
            try:
                from tokenizers import Tokenizer

                self._tok = Tokenizer.from_file(tokenizer_path)
                log.info("tokenizer loaded from %s", tokenizer_path)
            except Exception:  # a broken tokenizer must not take the gateway down
                log.exception("failed to load tokenizer from %s; using estimates", tokenizer_path)
        else:
            log.warning("no tokenizer at %r; token counts will be estimates", tokenizer_path)

    @property
    def source(self) -> str:
        return SOURCE_TOKENIZER if self._tok is not None else SOURCE_ESTIMATE

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._tok is None:
            return max(1, len(text) // 4)
        return len(self._tok.encode(text, add_special_tokens=False).ids)

    def count_messages(self, messages: list[dict]) -> int:
        total = _GENERATION_PROMPT_OVERHEAD
        for m in messages:
            total += self.count(_text_of(m.get("content"))) + _PER_MESSAGE_OVERHEAD
        return total
