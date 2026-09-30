"""
Generate short descriptions for landscape items with Claude.

Claude writes from its own knowledge and can use the Claude API's web search
tool when it doesn't know a technology well enough (newer or niche tools).
Descriptions go into the review file, so a human checks them before `apply`
writes them to data.yml.

Web search must be enabled for your organization in the Claude Console, and
each search is billed on top of tokens. Set CLAUDE_WEB_SEARCH=0 to turn it off.
"""

import os
import re

import helpers

DESCRIBE_BATCH_SIZE = 20
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

# Descriptions like "This is the description of Java" in the sample data.yml.
PLACEHOLDER = re.compile(r"^\s*this is the description of\b", re.IGNORECASE)

DESCRIBE_TOOL = {
    "name": "record_descriptions",
    "description": "Record a description for each technology.",
    "input_schema": {
        "type": "object",
        "properties": {
            "descriptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The name exactly as given."},
                        "description": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["name", "description", "confidence"],
                },
            }
        },
        "required": ["descriptions"],
    },
}

DESCRIBE_SYSTEM = """You write short descriptions of technologies for an internal skills landscape
page. Readers are developers and managers browsing which technologies the team knows.

<rules>
1. Write one or two sentences, at most 40 words: what the technology is and what it's
   mainly used for. Present tense, neutral tone.
2. No marketing language ("powerful", "leading", "best-in-class"), no version numbers,
   no release dates, no pricing, no statistics that go out of date.
3. Don't start with the technology's name; start with what it is
   (e.g., "Open-source relational database known for ...").
4. Write in your own words. Never copy sentences from websites or search results.
5. If you know the technology well, answer directly. Use web search only for
   technologies you don't recognize or aren't sure about, and use the homepage URL
   given, if any, to confirm you have the right one.
6. Set "confidence" to "low" if you still aren't sure what the technology is.
</rules>

When you're done, record all descriptions with the record_descriptions tool, one entry
per input, using each name exactly as given."""


def needs_description(text) -> bool:
    return not text or not str(text).strip() or bool(PLACEHOLDER.match(str(text)))


def _model() -> str:
    return os.environ.get("CLAUDE_MODEL", getattr(helpers, "DEFAULT_MODEL", "claude-sonnet-5"))


def _describe_batch(client, batch: list[tuple[str, str | None]], use_search: bool) -> list[dict]:
    lines = [f"- {name}" + (f" (homepage: {url})" if url else "") for name, url in batch]
    user = "Describe these technologies:\n" + "\n".join(lines)
    system = [{"type": "text", "text": DESCRIBE_SYSTEM, "cache_control": {"type": "ephemeral"}}]

    if use_search:
        messages = [{"role": "user", "content": user}]
        response = client.messages.create(
            model=_model(), max_tokens=8000, system=system,
            tools=[WEB_SEARCH_TOOL, DESCRIBE_TOOL], tool_choice={"type": "auto"},
            messages=messages,
        )
        # Long searches can pause mid-turn; send the partial turn back to continue it.
        for _ in range(5):
            if response.stop_reason != "pause_turn":
                break
            messages = messages + [{
                "role": "assistant",
                "content": [b.model_dump(exclude_none=True) for b in response.content],
            }]
            response = client.messages.create(
                model=_model(), max_tokens=8000, system=system,
                tools=[WEB_SEARCH_TOOL, DESCRIBE_TOOL], tool_choice={"type": "auto"},
                messages=messages,
            )
        for block in response.content:
            if block.type == "tool_use" and block.name == DESCRIBE_TOOL["name"]:
                return block.input.get("descriptions", [])
        # Claude answered in text instead of using the tool; fall through and force it.

    result = helpers.call_tool(client, DESCRIBE_SYSTEM, [{"type": "text", "text": user}], DESCRIBE_TOOL)
    return result.get("descriptions", [])


def generate_descriptions(items: list[tuple[str, str | None]]) -> dict[str, dict]:
    """items: (name, homepage_url) pairs. Returns {normalized name: {description, confidence}}."""
    if not items:
        return {}
    use_search = os.environ.get("CLAUDE_WEB_SEARCH", "1") != "0"
    client = helpers.claude_client()
    results = {}
    for i in range(0, len(items), DESCRIBE_BATCH_SIZE):
        batch = items[i:i + DESCRIBE_BATCH_SIZE]
        print(f"Writing descriptions {i + 1}-{i + len(batch)} of {len(items)}"
              f"{' (web search on)' if use_search else ''}...")
        for d in _describe_batch(client, batch, use_search):
            text = (d.get("description") or "").strip()
            if text:
                results[helpers.normalize(d.get("name", ""))] = {
                    "description": text, "confidence": d.get("confidence")}
    return results