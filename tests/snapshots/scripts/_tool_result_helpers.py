"""Helper :class:`ToolCallWidget` subclass that materialises its result on mount.

``ToolCallWidget.set_result`` mutates the child ``Static`` label that
:meth:`on_mount` creates — calling it from the constructor is a no-op
because the label doesn't exist yet.  :class:`_ResultToolCallWidget`
calls ``set_result`` immediately after the super-class has finished
mounting so snapshot boot scripts can declare the final state in one
place.
"""

from __future__ import annotations

from duh.ui.widgets import ToolCallWidget


class _ResultToolCallWidget(ToolCallWidget):
    """ToolCallWidget that applies a canned result as soon as it mounts."""

    def __init__(
        self,
        *args,
        result_output: str,
        result_is_error: bool = False,
        result_elapsed_ms: float | None = 1200.0,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._canned_output = result_output
        self._canned_is_error = result_is_error
        self._canned_elapsed_ms = result_elapsed_ms

    def on_mount(self) -> None:  # type: ignore[override]
        super().on_mount()
        self.set_result(
            output=self._canned_output,
            is_error=self._canned_is_error,
            style=self._output_style,
            elapsed_ms=self._canned_elapsed_ms,
        )
