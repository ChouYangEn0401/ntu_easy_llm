"""Normalize raw response objects from different LLM providers into plain strings.

Each LLM SDK returns a different response shape.  These helpers centralise the
extraction logic so every other module only calls one clean function.

    Provider            SDK call                           Parser
    ------------------  ---------------------------------  -------------------------
    OpenAI (new)        client.responses.create()          parse_openai_response()
    OpenAI (legacy)     client.chat.completions.create()   parse_openai_completion()
    Google Gemini       client.models.generate_content()   parse_gemini_response()
    Anthropic Claude    client.messages.create()           parse_anthropic_response()
"""
from __future__ import annotations


def parse_openai_response(resp) -> str:
    """Extract text from an OpenAI ``responses.create()`` response.

    Works for standard calls **and** web-search enabled calls.
    ``resp.output_text`` is the official convenience property of the
    OpenAI Responses API that merges all output items into a single string.
    """
    return (resp.output_text or "").strip()


def parse_openai_completion(resp) -> str:
    """Extract text from an OpenAI ``chat.completions.create()`` response.

    Use this only when you explicitly call the legacy Chat Completions API.
    """
    content = resp.choices[0].message.content
    return (content or "").strip()


def parse_gemini_response(resp) -> str:
    """Extract text from a Google Gemini ``models.generate_content()`` response.

    Returns an empty string when the response is blocked by safety filters.
    """
    return (resp.text or "").strip()


def parse_anthropic_response(resp) -> str:
    """Extract text from an Anthropic ``messages.create()`` response.

    Returns an empty string when the content list is unexpectedly empty.
    """
    if not resp.content:
        return ""
    return (resp.content[0].text or "").strip()
