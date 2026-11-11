"""
Centralized LLM provider selection.

LLM_PROVIDER (.env) picks the chat model every agent uses:
  - "ollama" (default, matches current behavior) -- OLLAMA_BASE_URL, OLLAMA_MODEL
  - "gemini" -- GOOGLE_API_KEY, GEMINI_MODEL

Agents call create_llm() and never construct ChatOllama /
ChatGoogleGenerativeAI themselves, so switching providers is a .env
change only.
"""

import os

from dotenv import load_dotenv

# Safe to call again; protects this module when imported standalone
# (a script, a test) before anything else has loaded .env.
load_dotenv()

# Shared limits so both providers behave the same.
LLM_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024"))

DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


def get_llm_provider() -> str:
    return os.getenv("LLM_PROVIDER", "ollama").strip().lower()


def get_llm_model_name() -> str:
    """Configured model name, without building a model (for logs/monitoring)."""
    if get_llm_provider() == "gemini":
        return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    return os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def create_llm():
    """Build the chat model for whichever provider LLM_PROVIDER selects."""
    provider = get_llm_provider()

    if provider == "ollama":
        # Imported lazily so a Gemini-only deployment doesn't need it.
        from langchain_ollama import ChatOllama

        return ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            temperature=0,
            # Ollama's own 2048-token default would silently truncate
            # large tool results before the final answer.
            num_ctx=LLM_NUM_CTX,
            num_predict=LLM_MAX_OUTPUT_TOKENS,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("LLM_PROVIDER=gemini but GOOGLE_API_KEY is not set")

        return ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            google_api_key=api_key,
            temperature=0,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        )

    raise ValueError(
        f"Unsupported LLM provider: {provider!r} (expected 'ollama' or 'gemini')"
    )
