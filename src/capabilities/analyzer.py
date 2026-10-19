from __future__ import annotations

from abc import ABC, abstractmethod

from src.schemas.agent import AnalyzerOutput
from src.schemas.responses import ParsedEmail


class Analyzer(ABC):
    name: str = "analyzer"

    @abstractmethod
    def analyze(self, email: ParsedEmail) -> AnalyzerOutput:
        """Analyse one email.
        May raise ModelError (or a subclass) when it depends on a model and
        the model fails. Must not raise for failing tools: those become ToolResults with status "unavailable".
        """