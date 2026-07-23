from urllib.parse import urlparse

_BLOCKED = {"localhost", "127.0.0.1"}


def is_safe_url(url):
    host = (urlparse(url).hostname or "").lower()
    return host not in _BLOCKED
