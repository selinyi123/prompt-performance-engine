"""Parse complete optimized Prompts from model responses."""

from __future__ import annotations

import re


TAGGED_PROMPT_RE = re.compile(
    r"<optimized_prompt>\s*(.*?)\s*</optimized_prompt>",
    flags=re.IGNORECASE | re.DOTALL,
)
FENCED_BLOCK_RE = re.compile(
    r"```(?:text|markdown|md|prompt)?\s*\r?\n(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)
HEADED_PROMPT_RE = re.compile(
    r"##\s*(?:优化后的\s*Prompt|Optimized\s+Prompt)\s*\r?\n+"
    r"```(?:text|markdown|md|prompt)?\s*\r?\n(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)


class PromptParseError(ValueError):
    pass


def extract_optimized_prompt(response: str, output_format: str) -> str:
    if not isinstance(response, str) or not response.strip():
        raise PromptParseError("Model response is empty.")

    tagged = TAGGED_PROMPT_RE.search(response)
    if tagged:
        prompt = tagged.group(1).strip()
        if prompt:
            return prompt

    headed = HEADED_PROMPT_RE.search(response)
    if headed:
        prompt = headed.group(1).strip()
        if prompt:
            return prompt

    blocks = [block.strip() for block in FENCED_BLOCK_RE.findall(response) if block.strip()]
    if output_format == "prompt_only" and len(blocks) == 1:
        return blocks[0]
    if blocks:
        return max(blocks, key=len)

    if output_format == "prompt_only":
        plain = response.strip()
        if plain and not plain.startswith(("#", "- ", "{")):
            return plain

    raise PromptParseError("No complete optimized Prompt could be extracted.")
