"""Which addresses other devices on the network can reach this machine at.

Used when the app is told to listen beyond this computer (``serve --network``,
``VJHSTUDIO_HOST=0.0.0.0``): "0.0.0.0" is a wildcard to listen on, not an address a
phone can open, so the startup line and the Settings page name the real ones."""

from __future__ import annotations

import contextlib
import socket

NETWORK_HOST = "0.0.0.0"  # noqa: S104 - the documented "listen on the network" address
WILDCARD_HOSTS = ("0.0.0.0", "::", "[::]", "*")  # noqa: S104 - matched, never bound here
LOOPBACK_PREFIXES = ("127.", "::1", "localhost")


def is_loopback(host: str) -> bool:
    return (host or "").strip().lower().startswith(LOOPBACK_PREFIXES)


def is_wildcard(host: str) -> bool:
    return (host or "").strip() in WILDCARD_HOSTS


def lan_addresses() -> list[str]:
    """This machine's IPv4 addresses on its networks, best guess first, never loopback.

    The first try asks the OS which interface it would use to reach another network: a
    UDP socket "connected" to a far address sends nothing, but gets bound to the right
    local address. ``gethostbyname(hostname)`` alone is not enough: on many Linux
    installs it answers 127.0.1.1."""
    found: list[str] = []
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        found.append(s.getsockname()[0])
    if found and not is_loopback(found[0]):
        # the address the OS routes through is the one other devices see; anything the
        # hostname also resolves to (a Docker bridge, a VPN, a VM network) would only
        # be a second address to try that does not work
        return found[:1]
    with contextlib.suppress(OSError):
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    return [a for a in dict.fromkeys(found) if a and not is_loopback(a) and a != "0.0.0.0"]  # noqa: S104


def network_urls(host: str, port: int) -> list[str]:
    """The URLs to type on another device, or ``[]`` when the app only answers on this
    computer. A specific non-loopback bind address is the one URL; a wildcard bind is
    every LAN address this machine has."""
    host = (host or "").strip()
    if not host or is_loopback(host):
        return []
    if is_wildcard(host):
        return [f"http://{a}:{port}/" for a in lan_addresses()]
    shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return [f"http://{shown}:{port}/"]
