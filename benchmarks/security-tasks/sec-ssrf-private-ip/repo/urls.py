from urllib.parse import urlparse


def is_safe_url(url):
    """Return True if url is allowed to be fetched by the server."""
    host = (urlparse(url).hostname or "").lower()
    return host != "localhost"
