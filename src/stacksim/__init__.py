"""stacksim - a didactic C interpreter that visualizes the stack and the heap."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("stacksim")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"
