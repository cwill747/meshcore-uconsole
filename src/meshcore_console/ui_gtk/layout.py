"""Proportional layout constants derived from screen width.

All panel widths are expressed as fractions of ``content_width`` so the
UI scales correctly when font sizes or screen dimensions change.
"""

from __future__ import annotations

from dataclasses import dataclass


# Fixed overhead inside each analyzer stream row (pixels):
#   panel-card border  2  (1px each side)
#   panel-card padding 28 (14px each side)
#   row border-left     3
#   box margin          8  (4px each side)
#   box spacing        24  (6px * 4 gaps between 5 columns)
_ANALYZER_ROW_OVERHEAD = 65


@dataclass(frozen=True, slots=True)
class Layout:
    """Compute pixel widths from proportional fractions of *content_width*.

    At the default 1264px content width (1280 screen - 16px padding) the
    computed values produce a layout that fits comfortably at 16px monospace.
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
    #
    # Column fractions are sized so that at 16px monospace (~10.5 px/char)
    # the natural text width of each column fits within its allocation.

    @property
    def analyzer_col_time(self) -> int:
        """11 chars  (HH:MM:SS.mm)"""
        return int(self.content_width * 0.092)

    @property
    def analyzer_col_type(self) -> int:
        """8 chars  (RESPONSE is the longest common type)"""
        return int(self.content_width * 0.067)

    @property
    def analyzer_col_node(self) -> int:
        """14 chars  (capped by set_max_width_chars)"""
        return int(self.content_width * 0.117)

    @property
    def analyzer_col_signal(self) -> int:
        """14 chars  (-112 / -9.00)"""
        return int(self.content_width * 0.11)

    @property
    def analyzer_col_content(self) -> int:
        """Remaining width after fixed columns and row overhead."""
        fixed = (
            self.analyzer_col_time
            + self.analyzer_col_type
            + self.analyzer_col_node
            + self.analyzer_col_signal
        )
        return max(100, self.content_width - fixed - _ANALYZER_ROW_OVERHEAD)

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
