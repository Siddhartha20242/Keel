"""One-shot chat completion for the Skeptic and the Reporter. Anthropic or any OpenAI-compatible endpoint, chosen by env.
Raises LLMUnavailable when no key is configured, so callers can degrade instead of crashing."""
from __future__ import annotations
import os, time


def have_key() -> bool:
    provider = os.environ.get("KEEL_PROVIDER") or ("openai" if os.environ.get("OPENAI_API_KEY") else "anthropic")
    return bool(os.environ.get("ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"))


class LLMUnavailable(Exception):
    pass


def complete(system: str, user: str, max_tokens: int = 700, retries: int = 3) -> str:
    provider = os.environ.get("KEEL_PROVIDER") or ("openai" if os.environ.get("OPENAI_API_KEY") else "anthropic")
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMUnavailable("no model key configured")
    if provider != "anthropic" and not os.environ.get("OPENAI_API_KEY"):
        raise LLMUnavailable("no model key configured")
    for attempt in range(retries):
        try:
            if provider == "anthropic":
                import anthropic
                r = anthropic.Anthropic().messages.create(model=os.environ.get("KEEL_MODEL", "claude-sonnet-5-5"), max_tokens=max_tokens,
                                                         system=system, messages=[{"role": "user", "content": user}])
                return "".join(b.text for b in r.content if b.type == "text").strip()
            import openai
            model = os.environ.get("KEEL_MODEL", "gpt-4o")
            extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
            r = openai.OpenAI().chat.completions.create(model=model, max_tokens=max_tokens, temperature=0, **extra,
                                                       messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            return (r.choices[0].message.content or "").strip()
        except Exception as e:                      # rate limits: wait and retry; anything else: surface
            code = getattr(e, "status_code", None)
            if code in (429, 503) and attempt < retries - 1:
                time.sleep(8 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")
