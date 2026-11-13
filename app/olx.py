import re
from urllib.parse import urlsplit, urlunsplit


_OLX_ADVERT = re.compile(r"/(?:kk/)?d/obyavlenie/(?:[^/]+-)?(?P<id>ID[A-Za-z0-9]+)\.html/?")


def normalize_olx_listing_url(value: str) -> str | None:
    """Keep the public advert path and remove tracking and language variants."""
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"olx.kz", "www.olx.kz"}
                or not _OLX_ADVERT.fullmatch(parsed.path)):
            return None
    except ValueError:
        return None
    path = parsed.path.removeprefix("/kk").rstrip("/")
    return urlunsplit(("https", "www.olx.kz", path, "", ""))


def olx_advert_id(url: str) -> str | None:
    normalized = normalize_olx_listing_url(url)
    if normalized is None:
        return None
    return _OLX_ADVERT.fullmatch(urlsplit(normalized).path).group("id")
