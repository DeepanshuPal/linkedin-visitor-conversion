"""Profile URL canonicalization, used as the lead dedupe key."""
from urllib.parse import urlsplit


def _is_linkedin(host):
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def normalize_url(url):
    """Return one canonical form per profile.

    Query strings, fragments, scheme, and trailing slashes are dropped. For
    LinkedIn, the same member shows up as linkedin.com, www.linkedin.com, or a
    regional host such as uk.linkedin.com, and the custom part of the URL is
    case-insensitive, so all of those collapse to https://www.linkedin.com/...
    in lowercase. Other hosts keep their path case.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    if _is_linkedin(netloc.split(":", 1)[0]):
        netloc = "www.linkedin.com"
        path = path.lower()
    return f"https://{netloc}{path}"
