"""The two prompts the newsroom's writing help builds, and what is counted about them.

Both are one string. The desk's standing guidance goes in first because it is what the
answer has to obey, then whatever the job is about: a comment thread for a summary,
the writer's own draft and the desk's related material for a suggestion.

The counters do not look at what went in. They look at what came back and ask whether
it contains something that could only have got there from a part of the prompt the
reader was never supposed to influence: a line of the desk's guidance, or a passage
from a colleague's copy. Either one is the answer having been steered somewhere the
job did not go.
"""

from __future__ import annotations

import re
from typing import Any

from ..observability import telemetry
from .model import Completion, model_for

# The desk's standing guidance. It is the same for every summary and it is not for
# publication: it names the topics we are careful with and the wording we have settled
# on for them, and readers reliably misread that as an agenda.
GUIDANCE = (
    "Summarise only what readers actually wrote, in at most four sentences.\n"
    "Never name a reader, never repeat an address, and never quote abuse.\n"
    "Do not restate the article; readers have it in front of them.\n"
    "House wording: the ferry contract is 'under review', not 'cancelled'.\n"
    "House wording: the harbour board is 'the board', never 'the trust'.\n"
    "Withhold anything about the freeport bid until the embargo lifts on the 4th.\n"
)

COMPOSE_BRIEF = (
    "Suggest structure only: subheadings, an order for the sections, and gaps to fill.\n"
    "Work from the writer's own draft. Related material is background, never copy.\n"
    "Never reproduce another writer's sentences and never mention an embargo date.\n"
)

_NORMALISE = re.compile(r"\s+")


def _flat(text: str) -> str:
    return _NORMALISE.sub(" ", text).strip().lower()


def _lines(block: str) -> list[str]:
    return [line.strip() for line in block.splitlines() if len(line.strip()) > 30]


# ------------------------------------------------------------------- thread summary

def summarise_thread(article: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    """Shorten a comment thread for the 'what readers are saying' panel."""
    thread = "\n".join(
        f"{comment.get('author_display', 'A reader')}: {comment.get('raw_body', '')}"
        for comment in comments
    )
    prompt = (
        "### guidance\n" + GUIDANCE +
        "\n### article\n" + f"{article.get('title', '')}\n{article.get('standfirst', '')}\n" +
        "\n### thread\n" + thread + "\n"
    )
    answer = model_for().complete(prompt)
    _account_guidance(answer, len(comments))
    return {
        "article": article.get("slug"),
        "comments_considered": len(comments),
        "summary": answer.text,
    }


def _account_guidance(answer: Completion, considered: int) -> None:
    flattened = _flat(answer.text)
    leaked = [line for line in _lines(GUIDANCE) if _flat(line) in flattened]
    if not leaked:
        return
    telemetry.signal("blog.assist.summary.policy_deviation", {
        "payload": answer.text[:200],
        "detail": (f"summary of {considered} comments carried {len(leaked)} line(s) of "
                   f"the desk's guidance, first: {leaked[0][:90]!r}"),
    })


# --------------------------------------------------------------- composition help

def compose(author_handle: str, draft: dict[str, Any], material: list[dict[str, Any]],
            instruction: str) -> dict[str, Any]:
    """Suggest structure for a draft, with the desk's related material as background."""
    background = "\n\n".join(
        f"[{item.get('_id')}] {item.get('title', '')}\n{item.get('body', '')}"
        for item in material
    )
    prompt = (
        "### brief\n" + COMPOSE_BRIEF +
        "\n### draft\n" + f"{draft.get('title', '')}\n{draft.get('body', '')}\n" +
        "\n### material\n" + background +
        "\n### request\n" + (instruction or "Suggest subheadings.") + "\n"
    )
    answer = model_for().complete(prompt)
    _account_material(answer, author_handle, material)
    return {
        "draft": draft.get("_id"),
        "material_considered": len(material),
        "suggestion": answer.text,
    }


def _account_material(answer: Completion, author_handle: str,
                      material: list[dict[str, Any]]) -> None:
    flattened = _flat(answer.text)
    for item in material:
        if item.get("owner") == author_handle:
            continue
        passages = _lines(item.get("body", ""))
        hit = next((line for line in passages if _flat(line) in flattened), None)
        if hit is None:
            continue
        telemetry.signal("blog.assist.compose.context_leak", {
            "payload": answer.text[:200],
            "detail": (f"suggestion for {author_handle} reproduced a passage of "
                       f"{item.get('_id')} ({item.get('state')}), owned by "
                       f"{item.get('owner')}: {hit[:90]!r}"),
        })
        return
