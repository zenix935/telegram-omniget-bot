import pytest
from core.security import is_ip_private, is_supported_media_domain, validate_url


def test_ip_private_ranges():
    assert is_ip_private("127.0.0.1") is True
    assert is_ip_private("10.0.0.5") is True
    assert is_ip_private("192.168.1.1") is True
    assert is_ip_private("172.16.0.1") is True
    assert is_ip_private("169.254.169.254") is True  # AWS metadata IP
    assert is_ip_private("0.0.0.0") is True
    assert is_ip_private("::1") is True
    assert is_ip_private("8.8.8.8") is False
    assert is_ip_private("1.1.1.1") is False


def test_validate_url_ssrf_and_schemes():
    # Valid public URLs (resolve_dns=False to avoid test flakiness)
    valid, err = validate_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", resolve_dns=False)
    assert valid is True
    assert err is None

    valid, err = validate_url("https://tiktok.com/@user/video/12345", resolve_dns=False)
    assert valid is True

    # Invalid schemes
    valid, err = validate_url("file:///etc/passwd", resolve_dns=False)
    assert valid is False
    assert "Unsupported scheme" in err

    valid, err = validate_url("ftp://server/video.mp4", resolve_dns=False)
    assert valid is False

    # Localhost and internal domains
    valid, err = validate_url("http://localhost:8000/media.mp4", resolve_dns=False)
    assert valid is False
    assert "Localhost" in err

    valid, err = validate_url("http://my-service.internal/video", resolve_dns=False)
    assert valid is False

    valid, err = validate_url("http://router.local/video", resolve_dns=False)
    assert valid is False

    # Direct private IP
    valid, err = validate_url("http://192.168.1.100/test.mp4", resolve_dns=False)
    assert valid is False


def test_is_supported_media_domain():
    assert is_supported_media_domain("https://www.youtube.com/watch?v=123") is True
    assert is_supported_media_domain("https://youtu.be/123") is True
    assert is_supported_media_domain("https://www.tiktok.com/@user/video/123") is True
    assert is_supported_media_domain("https://instagram.com/p/123") is True
    assert is_supported_media_domain("https://twitter.com/user/status/123") is True
    assert is_supported_media_domain("https://x.com/user/status/123") is True
    assert is_supported_media_domain("https://reddit.com/r/videos/comments/123") is True
    assert is_supported_media_domain("https://udemy.com/course/123") is True
    assert is_supported_media_domain("https://unknown-domain-xyz.org/file.mp4") is False
