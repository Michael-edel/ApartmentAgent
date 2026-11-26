"""Validate public listing image URLs before passing them to Telegram."""

from urllib.parse import urlsplit

KRISHA_PHOTO_HOSTS = frozenset({
    "photos.krisha.kz", "krisha-photos.kcdn.kz", "krisha-photos.kcdn.online", "kzala2-photos-kr.kcdn.kz",
})


def public_listing_photo(value: object, source: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("//"):
        value = f"https:{value}"
    if not value or any(char.isspace() for char in value) or len(value) > 2048:
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (url.scheme != "https" or url.port not in (None, 443) or url.username is not None
                or url.password is not None or not url.path or url.fragment):
            return None
    except ValueError:
        return None
    if source == "krisha":
        allowed = host in KRISHA_PHOTO_HOSTS
    elif source == "olx":
        allowed = host.endswith(".olxcdn.com")
    else:
        return None
    if not allowed or any(term in url.path.casefold() for term in ("empty-photo", "no-photo", "nophoto", "placeholder")):
        return None
    return value
