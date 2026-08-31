"""URL validation and SSRF (Server-Side Request Forgery) protection."""

import ipaddress
import re
import socket
from urllib.parse import urlparse
from typing import Tuple, Optional

# Supported major platforms
SUPPORTED_DOMAINS_REGEX = re.compile(
    r"^(?:.*\.)?(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|twitter\.com|x\.com|"
    r"reddit\.com|redd\.it|udemy\.com|facebook\.com|fb\.watch|vimeo\.com|"
    r"soundcloud\.com|spotify\.com|twitch\.tv|bilibili\.com|threads\.net|pinterest\.com|pin\.it)$",
    re.IGNORECASE,
)

# Private / local IP networks
FORBIDDEN_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_ip_private(ip_str: str) -> bool:
    """Check if an IP address string belongs to a private, loopback, or reserved range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_unspecified
            or any(ip in net for net in FORBIDDEN_NETWORKS)
        )
    except ValueError:
        return False


def validate_url(url: str, resolve_dns: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validates a URL against SSRF vulnerabilities, illegal schemes, and malicious hostnames.

    Returns:
        (is_valid: bool, error_reason: Optional[str])
    """
    if not url or not isinstance(url, str):
        return False, "Empty or invalid URL"

    url = url.strip()
    if len(url) > 2048:
        return False, "URL length exceeds maximum limit (2048 characters)"

    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Failed to parse URL: {e}"

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Unsupported scheme: {parsed.scheme}. Only HTTP/HTTPS are allowed."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing valid hostname"

    hostname_lower = hostname.lower()

    # Reject localhost & known local names
    if hostname_lower in ("localhost", "localhost.localdomain", "broadcasthost", "local"):
        return False, "Localhost targets are not allowed."

    if hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
        return False, "Internal / mDNS domain targets are not allowed."

    # Check if host is direct IP
    try:
        if is_ip_private(hostname):
            return False, f"Private or reserved IP target is not allowed: {hostname}"
    except Exception:
        pass

    # Resolve hostname to check resolved IP for SSRF
    if resolve_dns:
        try:
            addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            for item in addr_info:
                ip_resolved = item[4][0]
                if is_ip_private(ip_resolved):
                    return False, f"Hostname resolves to private/prohibited IP: {ip_resolved}"
        except socket.gaierror:
            return False, f"Could not resolve host: {hostname}"
        except Exception as e:
            return False, f"DNS resolution check failed: {e}"

    return True, None


def is_supported_media_domain(url: str) -> bool:
    """
    Check if the URL belongs to a known supported domain.
    yt-dlp and omniget support thousands of sites, but this helper can identify standard platforms.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return bool(SUPPORTED_DOMAINS_REGEX.match(host))
    except Exception:
        return False
