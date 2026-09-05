"""Writing help for the newsroom: thread summaries and composition suggestions."""

from .model import Completion, LocalNarrativeModel, model_for
from .prompts import compose, summarise_thread

__all__ = ["Completion", "LocalNarrativeModel", "compose", "model_for", "summarise_thread"]
