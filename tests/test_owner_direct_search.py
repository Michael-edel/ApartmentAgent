from urllib.parse import parse_qs, urlparse

import pytest

from app.krisha_search import build_krisha_search_url, parse_krisha_search_page
from app import search_agent
from app.search_providers import SearchItem


def test_owner_filter_parameter_is_opt_in():
    assert parse_qs(urlparse(build_krisha_search_url(rooms=3, owner_only=True)).query)["das[who]"] == ["1"]
    assert "das[who]" not in parse_qs(urlparse(build_krisha_search_url(rooms=3)).query)


def test_owner_mark_is_from_matching_card_and_not_new_building_or_unrelated_text():
    html = '''
    <div class="a-card" data-id="111"><div class="a-card__main-info">
      <a class="a-card__title" href="/a/show/111">3-комнатная квартира 80 м²</a>
    </div><div class="a-card__footer user-owner"><div class="label label-user-owner">Хозяин недвижимости</div></div></div>
    <div class="a-card" data-id="222"><div class="a-card__main-info">
      <a class="a-card__title" href="/a/show/222">3-комнатная квартира 85 м²</a>
    </div><div class="a-card__footer user-complex">Новостройка от застройщика</div></div>
    <div class="a-card" data-id="999"><div class="a-card__main-info">
      <a class="a-card__title" href="/a/show/333">3-комнатная квартира 90 м²</a>
    </div><div class="label-user-owner">Хозяин недвижимости</div></div>
    <a href="/a/show/444">Хозяин недвижимости</a>
    '''
    roles = {item.url: item.seller_type for item in parse_krisha_search_page(html)}
    assert roles["https://krisha.kz/a/show/111"] == "Хозяин недвижимости"
    assert roles["https://krisha.kz/a/show/222"] is None
    assert roles["https://krisha.kz/a/show/333"] is None
    assert roles["https://krisha.kz/a/show/444"] is None


def test_policy_notice_does_not_hide_results_but_real_challenge_does():
    card = '<a class="a-card__title" href="/a/show/111">3-комнатная квартира 80 м²</a>'
    notice = '<p class="g-recaptcha-policy">Этот сайт защищён сервисом reCAPTCHA</p>'
    assert len(parse_krisha_search_page(card + notice)) == 1
    assert parse_krisha_search_page(card + notice + '<div>CAPTCHA: проверка безопасности</div>') == []


@pytest.mark.asyncio
async def test_one_off_owner_search_uses_only_direct_provider(monkeypatch):
    seen = []
    saved = []
    async def fetch(provider, query):
        seen.append((provider, query))
        return provider, query, [SearchItem("owner", "https://krisha.kz/a/show/1", seller_type="Хозяин недвижимости"),
                                 SearchItem("developer", "https://krisha.kz/a/show/2")], None
    async def save(provider, query, items):
        saved.extend(items)
        return 0, 0, []
    async def auto_import(*args):
        return 0, 0, 0
    monkeypatch.setattr(search_agent.settings, "search_enabled", True)
    monkeypatch.setattr(search_agent.settings, "search_providers", ["brave", "bing_rss"])
    monkeypatch.setattr(search_agent.settings, "target_rooms", 3)
    monkeypatch.setattr(search_agent, "_fetch_one", fetch)
    monkeypatch.setattr(search_agent, "_save_items", save)
    monkeypatch.setattr(search_agent, "_auto_import", auto_import)
    result = await search_agent._run_search(seller="owner")
    assert seen == [("krisha_direct", "owner:3")]
    assert result.queries == 1
    assert [item.url for item in saved] == ["https://krisha.kz/a/show/1"]
