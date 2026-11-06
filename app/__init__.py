"""ApartmentAgent application package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("apartment-agent")
except PackageNotFoundError:
    __version__ = "development"
