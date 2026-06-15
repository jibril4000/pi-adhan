"""Minimal sd_notify client for the systemd watchdog (no external deps).

Talks to systemd over the NOTIFY_SOCKET protocol to report readiness and to
send periodic watchdog keep-alives. When NOTIFY_SOCKET is unset (manual runs,
tests, or any non-systemd launch) every method is a silent no-op, so behaviour
is unchanged outside systemd.
"""

from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger("adhan.systemd")


class SystemdNotifier:
    """Sends READY=1 and WATCHDOG=1 datagrams to systemd, if available."""

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._addr: str | None = None

        addr = os.environ.get("NOTIFY_SOCKET")
        if not addr:
            return

        # Abstract-namespace sockets are reported with a leading '@'.
        if addr.startswith("@"):
            addr = "\0" + addr[1:]

        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self._addr = addr
        except OSError as e:
            logger.debug("sd_notify unavailable: %s", e)
            self._sock = None

    @property
    def enabled(self) -> bool:
        return self._sock is not None

    def _send(self, message: str) -> None:
        if not self._sock or self._addr is None:
            return
        try:
            self._sock.sendto(message.encode(), self._addr)
        except OSError as e:
            logger.debug("sd_notify send failed: %s", e)

    def ready(self) -> None:
        """Tell systemd startup is complete (required for Type=notify)."""
        self._send("READY=1")

    def watchdog(self) -> None:
        """Send a watchdog keep-alive ping."""
        self._send("WATCHDOG=1")
