"""TLS helpers for outbound calls from Foundry hosted runtimes."""

from __future__ import annotations

import ssl


def hosted_server_tls_context() -> ssl.SSLContext:
    """Use the hosted runtime's trusted CA store without enabling proxy discovery."""

    return ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
