"""Proportional layout constants derived from screen width.

All panel widths are expressed as fractions of ``content_width`` so the
UI scales correctly when font sizes or screen dimensions change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Layout:
    """Compute pixel widths from proportional fractions of *content_width*.

    At the default 1264px content width (1280 screen - 16px padding) the
    computed values are nearly identical to the previous hardcoded sizes.
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
    def analyzer_col_time(self) -> int:
        return int(self.content_width * 0.071)

    @property
    def analyzer_col_type(self) -> int:
        return int(self.content_width * 0.063)

    @property
    def analyzer_col_node(self) -> int:
        return int(self.content_width * 0.095)

    @property
    def analyzer_col_signal(self) -> int:
        return int(self.content_width * 0.075)

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
