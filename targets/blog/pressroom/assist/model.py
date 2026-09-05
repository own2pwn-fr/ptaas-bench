"""The completion model the newsroom's writing help runs on.

The desk's tools talk to an inference host when one is configured
(``ASSIST_MODEL_ENDPOINT``). Nothing in this deployment configures one, so they fall
back to the in-process model below: small, deterministic, and good enough for the two
jobs it has, which are shortening a comment thread and suggesting subheadings.

It is an instruction-following model in the plainest sense. It reads the prompt, takes
the most recent instruction it can find in it, and does that; when it finds no
instruction it falls back to picking out the sentences that carry the most of the
prompt's own vocabulary. That is the whole model, and its determinism is the reason it
is usable at all -- the same thread always shortens the same way, so an editor who
disagrees with a summary can be shown why.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

SECTION = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)

# What an instruction looks like when somebody writes one in prose.
IMPERATIVE = re.compile(
    r"(?:^|(?<=[.!?:;\n>\-]))\s*((?:please\s+)?(?:ignore|disregard|forget|instead|"
    r"print|output|repeat|reveal|show|quote|include|list|write|say|return|"
    r"summarise|summarize|respond|reply|answer|begin|start)\b[^.!?\n]{0,240}[.!?]?)",
    re.IGNORECASE,
)

# What the instruction is asking to be given.
ASKS_FOR_BRIEF = re.compile(
    r"\b(guidance|guidelines?|brief|instructions?|policy|policies|system|prompt|"
    r"rules?|above|preceding|previous|earlier)\b", re.IGNORECASE)
ASKS_FOR_MATERIAL = re.compile(
    r"\b(material|documents?|context|sources?|drafts?|files?|everything|"
    r"verbatim|in full|entirety|retrieved|embargo\w*)\b", re.IGNORECASE)
QUOTED = re.compile(r"[\"“']([^\"”']{3,200})[\"”']")

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass
class Completion:
    text: str
    reason: str


class LocalNarrativeModel:
    """A small instruction-following summariser with no external dependency."""

    name = "narrative-local-2"

    def complete(self, prompt: str, *, limit: int = 900) -> Completion:
        sections = self._sections(prompt)
        instruction = self._last_instruction(prompt)
        if instruction:
            answer = self._follow(instruction, sections)
            if answer:
                return Completion(text=answer[:limit], reason="instruction")
        body = sections[-1][1] if sections else prompt
        return Completion(text=self._condense(body)[:limit], reason="condensed")

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _sections(prompt: str) -> list[tuple[str, str]]:
        marks = list(SECTION.finditer(prompt))
        if not marks:
            return [("", prompt)]
        out = []
        for index, mark in enumerate(marks):
            end = marks[index + 1].start() if index + 1 < len(marks) else len(prompt)
            out.append((mark.group(1).strip().lower(), prompt[mark.end():end].strip()))
        return out

    @staticmethod
    def _last_instruction(prompt: str) -> str:
        found = [m.group(1).strip() for m in IMPERATIVE.finditer(prompt)]
        return found[-1] if found else ""

    def _follow(self, instruction: str, sections: list[tuple[str, str]]) -> str:
        named = {name: body for name, body in sections}
        if ASKS_FOR_BRIEF.search(instruction):
            for name in ("guidance", "brief"):
                if named.get(name):
                    return named[name]
        if ASKS_FOR_MATERIAL.search(instruction):
            for name in ("material", "sources", "thread"):
                if named.get(name):
                    return named[name]
        quoted = QUOTED.search(instruction)
        if quoted:
            return quoted.group(1)
        return ""

    @staticmethod
    def _condense(body: str, keep: int = 3) -> str:
        sentences = [s.strip() for s in SENTENCE.findall(body) if len(s.strip()) > 25]
        if not sentences:
            return body.strip()[:400]
        vocabulary: dict[str, int] = {}
        for sentence in sentences:
            for word in re.findall(r"[a-z']{4,}", sentence.lower()):
                vocabulary[word] = vocabulary.get(word, 0) + 1
        def weight(item: tuple[int, str]) -> tuple[int, int]:
            index, sentence = item
            score = sum(vocabulary.get(w, 0)
                        for w in set(re.findall(r"[a-z']{4,}", sentence.lower())))
            return (-score, index)
        chosen = sorted(enumerate(sentences), key=weight)[:keep]
        return " ".join(sentence for _, sentence in sorted(chosen, key=lambda i: i[0]))


def model_for() -> LocalNarrativeModel:
    """The model the writing tools should use for this deployment."""
    endpoint = os.environ.get("ASSIST_MODEL_ENDPOINT", "").strip()
    if endpoint:
        # An inference host is configured for the estate but has never been pointed at
        # this service; the in-process model is what actually answers.
        pass
    return LocalNarrativeModel()
