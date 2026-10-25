"""Optional language-model backend (Azure OpenAI in Microsoft Foundry).

Configured by environment variables; if they are missing, Touchline runs fully
offline with template lines. Nothing else in the app depends on this module.

    AZURE_OPENAI_ENDPOINT      e.g. https://<resource>.openai.azure.com/
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_DEPLOYMENT    the deployment name of a chat model
    AZURE_OPENAI_API_VERSION   optional, default 2024-10-21
"""

import json
import os
import time

SYSTEM_PROMPT = """You are the co-commentator on a live football radio broadcast for blind and low-vision listeners.
The play-by-play commentator handles the action; you add context during stoppages.

Rules:
- Use ONLY the facts in the JSON you are given. Never invent numbers, names, history, injuries or quotes.
- Every number you say must appear in the facts. If you are unsure, leave the number out.
- One or two short sentences, at most 35 words in total. Plain spoken English, no lists, no hashtags.
- Don't repeat what was just said (see "recently_said").
- Describe chance quality in words ("the better chances") rather than saying "xG" or "expected goals".
- If nothing in the facts is worth saying, reply with exactly: SKIP"""


class AzureLLM:
    def __init__(self):
        from openai import AzureOpenAI   # imported lazily so offline mode needs no dependency
        self.client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        self.name = f"azure-openai:{self.deployment}"

    def colour(self, trigger, facts, recently_said):
        payload = {"moment": trigger, "facts": facts, "recently_said": recently_said[-4:]}
        t0 = time.time()
        resp = self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            temperature=0.7,
            max_tokens=90,
        )
        text = (resp.choices[0].message.content or "").strip().strip('"')
        return text, time.time() - t0


def available():
    """Azure OpenAI in Microsoft Foundry is configured."""
    return all(os.environ.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"))


def get_backend():
    """The co-commentator's model: Agent Framework workflow first, the plain OpenAI SDK second, else templates."""
    if not available():
        return None
    try:
        from .agents import AgentFrameworkColour
        return AgentFrameworkColour()
    except Exception as exc:
        print(f"[touchline] Agent Framework unavailable ({exc}); trying the OpenAI SDK directly.")
    if not os.environ.get("AZURE_OPENAI_ENDPOINT"):
        return None
    try:
        return AzureLLM()
    except Exception as exc:              # missing package or bad config: fall back, but say why
        print(f"[touchline] Azure OpenAI unavailable ({exc}); using templates.")
    return None
