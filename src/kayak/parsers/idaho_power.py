"""Idaho Power parser (replaces Parse_IdahoPower.C).

Currently a stub — the C++ version had no actual parsing implementation.
"""

from __future__ import annotations

import logging

from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register

logger = logging.getLogger(__name__)


@register("idahoPower")
class IdahoPowerParser(BaseParser):
    name = "idahoPower"

    def parse(self, text: str) -> int:
        # C++ version used serveUpCookedLines but line() was a no-op
        return self.parse_cooked(text)

    def parse_line(self, line: str) -> bool:
        # Stub — no parsing implemented in C++ original either
        return True
