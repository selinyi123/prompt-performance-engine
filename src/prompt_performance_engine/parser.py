"""Parse complete optimized Prompts from the canonical JSON transport."""

from __future__ import annotations

from .contracts import parse_strict_json_object


SUPPORTED_OUTPUT_FORMATS = {"prompt_only", "standard", "evaluation_package"}


class PromptParseError(ValueError):
    pass


def extract_optimized_prompt(response: str, output_format: str) -> str:
    if not isinstance(response, str) or not response.strip():
        raise PromptParseError("Model response is empty.")
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise PromptParseError(f"Unsupported output_format: {output_format!r}.")

    try:
        transport = parse_strict_json_object(
            response,
            label="model optimization response",
        )
    except (TypeError, ValueError) as exc:
        raise PromptParseError(str(exc)) from exc
    if set(transport) != {"optimized_prompt"}:
        raise PromptParseError(
            "Model response must contain exactly the optimized_prompt JSON field."
        )
    prompt = transport["optimized_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise PromptParseError("optimized_prompt JSON field must be a non-empty string.")
    return prompt
