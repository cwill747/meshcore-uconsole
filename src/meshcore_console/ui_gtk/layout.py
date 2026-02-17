"""Proportional layout constants derived from screen width.

All panel widths are expressed as fractions of ``content_width`` so the
UI scales correctly when font sizes or screen dimensions change.

Analyzer stream columns use character-based sizing (set_width_chars /
set_max_width_chars) directly in AnalyzerView, not pixel fractions here,
because they render in a monospace font and must scale with the actual
character advance width.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Layout:
    """Compute pixel widths from proportional fractions of *content_width*.

    At the default 1264px content width (1280 screen - 16px padding) the
    computed values produce a layout that fits the target display.
    """

    content_width: int = 1264

    # -- Peers view ----------------------------------------------------------
    @property
    def peers_list_width(self) -> int:
        return int(self.content_width * 0.27)

    # -- Messages view -------------------------------------------------------
    @property
    def messages_channel_width(self) -> int:
        return int(self.content_width * 0.13)

    # -- Analyzer view -------------------------------------------------------
    @property
    def analyzer_details_width(self) -> int:
        return int(self.content_width * 0.174)

    # -- Map view ------------------------------------------------------------
    @property
    def map_details_width(self) -> int:
        return int(self.content_width * 0.206)

    # -- Widgets -------------------------------------------------------------
    @property
    def status_card_width(self) -> int:
        return int(self.content_width * 0.15)

    @property
    def detail_block_wrap_chars(self) -> int:
        return max(16, int(self.content_width * 0.019))
