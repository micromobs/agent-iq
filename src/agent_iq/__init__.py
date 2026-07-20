"""Agent IQ product package.

Importing this package is deliberately side-effect free. Runtime composition,
configuration loading, identity, and network clients are all explicit.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-iq")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.0.0"

__all__ = ["__version__"]
