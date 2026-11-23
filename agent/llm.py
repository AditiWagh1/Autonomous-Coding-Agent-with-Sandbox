"""
agent/llm.py - Model provider adapter for Google Gemini (free tier).
"""

import os
import time
from typing import List, Dict, Tuple, Optional

from .parsing import parse_response


class LLMClient:
    def __init__(
        self,
        model_name: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        api_key: Optional[str] = None,
        max_api_retries: int = 3,
        fallback_models: Optional[List[str]] = None,
    ):
        # Imported lazily so the rest of the project (and unit tests) work
        # without the google-genai package installed.
        from google import genai
        from google.genai import types

        self.model_name = model_name
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set. Set it in your environment or pass it to LLMClient.")
        self.client = genai.Client(api_key=self.api_key)
        self._types = types
        self.max_api_retries = max_api_retries
        # Tried in order when the main model stays overloaded (503) or rate-limited (429).
        env_fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
        self.fallback_models = [m for m in (fallback_models if fallback_models is not None else env_fallbacks) if m != model_name]
        self.last_model_used = model_name

    def query(self, messages: List[Dict[str, str]], temperature: float = 0.2, json_mode: bool = False) -> Tuple[str, int, int]:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]"""
        t = self._types
        system_text = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            t.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[t.Part(text=m["content"])],
            )
            for m in messages
            if m["role"] != "system"
        ]
        config = t.GenerateContentConfig(
            system_instruction=system_text or None,
            temperature=temperature,
            response_mime_type="application/json" if json_mode else None,
        )

        response, last_error = None, None
        for model in [self.model_name] + self.fallback_models:
            delay = 3.0
            for attempt in range(self.max_api_retries):
                try:
                    response = self.client.models.generate_content(model=model, contents=contents, config=config)
                    self.last_model_used = model
                    break
                except Exception as e:  # busy servers and free-tier limits are temporary
                    last_error = e
                    transient = any(x in str(e) for x in ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE"))
                    if not transient:
                        raise
                    if attempt < self.max_api_retries - 1:
                        time.sleep(delay)
                        delay *= 2
            if response is not None:
                break
        if response is None:
            raise last_error

        content = response.text or ""
        usage = response.usage_metadata
        p_tok = (usage.prompt_token_count or 0) if usage else 0
        c_tok = (usage.candidates_token_count or 0) if usage else 0
        return content, p_tok, c_tok

    # Kept for backwards compatibility
    @staticmethod
    def parse_response(raw: str, language: str = "python"):
        return parse_response(raw, language)

    @staticmethod
    def extract_python_code(raw_response: str) -> str:
        return parse_response(raw_response)[0]
