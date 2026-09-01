"""Gemini call through its OpenAI-compatible endpoint."""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY"),
    base_url=GEMINI_BASE_URL,
)


def call_agent(prompt: str) -> dict:
    """Send one prompt, expect a JSON object back, return it parsed."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""
    text = raw.strip()

    # Some models wrap JSON in a fenced block despite response_format.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model did not return valid JSON ({e}). Raw response:\n{raw}"
        ) from e
