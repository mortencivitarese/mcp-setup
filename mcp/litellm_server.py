#!/usr/bin/env python3
import os
os.environ["LITELLM_LOG"] = "ERROR"
os.environ["NO_COLOR"] = "1"
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import litellm

load_dotenv(Path(__file__).parent / ".env")

mcp = FastMCP("LiteLLM Gateway")


@mcp.tool()
def call_llm(model: str, prompt: str, system_prompt: str = "") -> str:
    """Call any LLM via LiteLLM.

    Model examples:
    - OpenAI:     gpt-4o, gpt-4o-mini, o3-mini
    - Anthropic:  anthropic/claude-opus-4-5, anthropic/claude-sonnet-4-6
    - Google:     gemini/gemini-2.0-flash, gemini/gemini-2.5-pro
    - Groq:       groq/llama-3.3-70b-versatile  (fast + free tier)
    - Ollama:     ollama/llama3.2  (local, no API key needed)
    - OpenRouter: openrouter/openai/gpt-4o
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    response = litellm.completion(model=model, messages=messages)
    return response.choices[0].message.content


@mcp.tool()
def list_available_models() -> str:
    """List model strings you can pass to call_llm, grouped by provider."""
    return """
OpenAI         : gpt-4o | gpt-4o-mini | gpt-4-turbo | o1-mini | o3-mini
                 Needs: OPENAI_API_KEY

Anthropic      : anthropic/claude-opus-4-5 | anthropic/claude-sonnet-4-6 | anthropic/claude-haiku-4-5
                 Needs: ANTHROPIC_API_KEY

Google Gemini  : gemini/gemini-2.0-flash | gemini/gemini-2.5-pro
                 Needs: GEMINI_API_KEY

Groq (fast)    : groq/llama-3.3-70b-versatile | groq/mixtral-8x7b-32768 | groq/gemma2-9b-it
                 Needs: GROQ_API_KEY  (free tier available at console.groq.com)

Ollama (local) : ollama/llama3.2 | ollama/mistral | ollama/qwen2.5
                 Needs: Ollama running locally (ollama.ai) — no API key

OpenRouter     : openrouter/openai/gpt-4o | openrouter/google/gemini-2.0-flash
                 Needs: OPENROUTER_API_KEY  (openrouter.ai)

Add API keys to: C:\\Users\\MBOL\\.claude\\mcp\\.env
"""


if __name__ == "__main__":
    mcp.run()
