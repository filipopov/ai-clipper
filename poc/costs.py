from __future__ import annotations

# Pricing as of 2026-04 (USD)
# Claude
CLAUDE_PRICING = {
    "claude-sonnet-4-6":       {"input": 3.00, "output": 15.00},  # per 1M tokens
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    # fallback
    "default":                 {"input": 3.00, "output": 15.00},
}

# Whisper
WHISPER_COST_PER_MINUTE = 0.006  # USD


def claude_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Returns cost in USD."""
    pricing = CLAUDE_PRICING.get(model, CLAUDE_PRICING["default"])
    return (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]


def whisper_cost(audio_duration_seconds: float) -> float:
    """Returns cost in USD."""
    return (audio_duration_seconds / 60) * WHISPER_COST_PER_MINUTE


def format_cost(usd: float) -> str:
    """Format cost for display."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"
