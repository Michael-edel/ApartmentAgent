from typing import Literal


MarketType = Literal["new", "secondary"]
MarketFilter = Literal["all", "new", "secondary"]


def matches_market_type(selected: MarketFilter, actual: MarketType | None) -> bool:
    return selected == "all" or actual == selected
