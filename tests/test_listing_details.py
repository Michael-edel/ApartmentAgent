import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service, main
from app.importer import import_krisha_listing
from app.models import Base
from app.schemas import ListingCreate


def payload(**kwargs):
    return ListingCreate(source="browser", source_url="https://krisha.kz/a/show/123",
                         title="Тестовая квартира", price_kzt=38_000_000, area_m2=96, rooms=4, **kwargs)


def test_details_input_is_plain_bounded_and_timestamp_is_response_only():
    value = payload(property_details={"<b>Ремонт</b>": "<strong>Хороший</strong> &amp; аккуратный"},
                    repair_condition=" <i>Хороший</i> ", details_extracted=True,
                    details_imported_at="2020-01-01T00:00:00Z")
    assert value.property_details == {"Ремонт": "Хороший & аккуратный"}
    assert value.repair_condition == "Хороший" and value.details_extracted
    assert "details_extracted" not in value.model_dump() and "details_imported_at" not in value.model_dump()
    for details in ({"x" * 101: "value"}, {"label": "x" * 1501}, {str(i): "v" for i in range(61)}):
        with pytest.raises(ValidationError):
            payload(property_details=details)


def test_old_extension_placeholder_is_not_saved_as_an_apartment_photo():
    value = payload(photo_urls=["https://krisha.kz/static/frontend/images/empty-photo-750.png",
                                "https://photos.example/real.jpg"])
    assert value.photo_urls == ["https://photos.example/real.jpg"]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_browser_endpoint_corrects_office_address_from_old_extensions(monkeypatch, explicit):
    async def save(value, *, allow_existing):
        assert allow_existing is True
        return value
    monkeypatch.setattr(main, "_save_listing", save)
    value = payload(address="Явный адрес 7" if explicit else "Астана, Нура р-н, пр. Улы Дала, 13",
                    property_details={"Адрес": "Явный адрес 7"} if explicit else {})
    value = value.model_copy(update={"title": "4-комнатная квартира · 95.66 м², Айтматова 21"})
    result = await main.import_listing_from_browser(value)
    assert result.address == (value.address if explicit else "Айтматова 21")
    assert result.price_kzt == value.price_kzt and result.source == "browser"


@pytest.mark.asyncio
async def test_detail_snapshot_replaces_properties_and_old_import_preserves_full_text_photos(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, "SessionLocal", sessions)
    saved = await listing_service.persist_listing(payload(
        description="Полный текст объявления о планировке и ремонте", repair_condition="Хороший ремонт",
        property_details={"Ремонт": "Хороший", "Балкон": "Есть"}, details_extracted=True,
        photo_urls=["https://example.test/photo1.jpg", "https://example.test/photo2.jpg"]), notify=False, refresh_ai=False)
    first_stamp = saved.row.details_imported_at
    old = payload(description="Короткая мета-строка", photo_urls=["https://example.test/preview.jpg"])
    old.price_kzt = 37_000_000
    result = await listing_service.persist_listing(old, notify=False, refresh_ai=False)
    assert result.row.price_kzt == 37_000_000
    assert result.row.description == "Полный текст объявления о планировке и ремонте"
    assert result.row.photo_urls == ["https://example.test/photo1.jpg", "https://example.test/photo2.jpg"]
    assert result.row.repair_condition == "Хороший ремонт"
    assert result.row.property_details == {"Ремонт": "Хороший", "Балкон": "Есть"}
    assert result.row.details_imported_at == first_stamp
    fresh = await listing_service.persist_listing(payload(property_details={"Ремонт": "Средний"},
        repair_condition="Средний", details_extracted=True), notify=False, refresh_ai=False)
    assert fresh.row.property_details == {"Ремонт": "Средний"}  # old balcony key must not survive
    assert fresh.row.details_imported_at >= first_stamp
    response = main._to_response(fresh.row)
    assert response.details_imported_at == fresh.row.details_imported_at
    assert response.property_details == {"Ремонт": "Средний"}
    assert "details_extracted" not in response.model_dump()
    manual = payload(description="Описание, уточнённое пользователем")
    manual.source = "manual"
    edited = await listing_service.persist_listing(manual, notify=False, refresh_ai=False)
    assert edited.row.description == "Описание, уточнённое пользователем"
    assert edited.row.details_imported_at == fresh.row.details_imported_at
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_details_columns_default_empty_without_claiming_import():
    from app.schema_migrations import ensure_legacy_columns

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for column in ("repair_condition", "property_details", "details_imported_at"):
            await connection.execute(text(f"ALTER TABLE listings DROP COLUMN {column}"))
        await connection.run_sync(ensure_legacy_columns)
        rows = (await connection.execute(text("PRAGMA table_info(listings)"))).all()
        assert {"repair_condition", "property_details", "details_imported_at"}.issubset({row[1] for row in rows})
        detail_column = next(row for row in rows if row[1] == "property_details")
        assert detail_column[3] == 1 and detail_column[4] == "'{}'"
    await engine.dispose()


@pytest.mark.asyncio
async def test_server_full_import_uses_explicit_public_details_and_advert_description(monkeypatch):
    html = '''
    <meta property="og:title" content="4-комнатная квартира, Астана">
    <meta property="og:description" content="Короткая мета-строка">
    <script type="application/ld+json">{"id":123,"price":38000000,"area":96,"rooms":4,"floor":1,
      "floorsTotal":5,"yearBuilt":1990,"mortgageSupported":true,"districtName":"Старый район"}</script>
    <dl><dt>Город</dt><dd>Астана, Алматы р-н</dd><dt>Адрес</dt><dd>Кудайбердыулы 36/3</dd>
    <dt>Этаж</dt><dd>3 из 9</dd><dt>Год постройки</dt><dd>2011</dd>
    <dt>Тип дома</dt><dd>кирпичный</dd><dt>Санузел</dt><dd>2 с/у и более</dd>
    <dt>Состояние квартиры</dt><dd>не новый, но аккуратный ремонт</dd><dt>В ипотеку</dt><dd>Нет</dd>
    <dt>Балкон</dt><dd>лоджия</dd></dl>
    <div class="offer__description"><p>Просторная квартира с хорошей планировкой.</p>
      <p>Кухня и две спальни выходят во двор.</p></div>
    <div><div>Автор объявления</div><h3>Анна</h3><p>Хозяин недвижимости</p><span hidden>+77001234567</span></div>
    <aside>Реклама: монолитный дом, ипотека: да, чужая квартира.</aside>
    <section><h3>Цена м² в похожих квартирах</h3>
      <div>845 700 ₸ Похожие квартиры в Есильском р-н</div>
      <div>792 420 ₸ Похожие квартиры в Астане</div></section>
    '''

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = await import_krisha_listing("https://krisha.kz/a/show/123")
    assert result.details_extracted
    assert result.description == "Просторная квартира с хорошей планировкой.\n\nКухня и две спальни выходят во двор."
    assert result.repair_condition == "не новый, но аккуратный ремонт"
    assert result.property_details["Балкон"] == "лоджия"
    assert (result.floor, result.floors_total, result.building_year, result.building_type) == (3, 9, 2011, "brick")
    assert (result.city, result.district, result.address) == ("Астана", "Алматы район", "Кудайбердыулы 36/3")
    assert (result.seller_name, result.seller_type) == ("Анна", "Хозяин недвижимости")
    assert result.mortgage_supported is False
    assert (result.bathrooms_count, result.bathrooms_count_is_lower_bound) == (2, True)
    assert result.krisha_district_price_per_m2 == 845700
    assert result.krisha_city_price_per_m2 == 792420
    assert "+7700" not in str(result.model_dump()) and "чужая квартира" not in result.description


def test_advert_prose_keeps_paragraph_br_and_raw_line_breaks_without_changing_properties():
    from app.importer import _advert_description, _public_details

    parser, details = _public_details('''<div class="offer__description"><p>Первый абзац<br>Ещё строка</p>
      <p>Второй
      абзац</p></div><dl><dt>Ремонт</dt><dd>Хороший
      аккуратный</dd></dl>''')
    assert _advert_description(parser) == "Первый абзац\nЕщё строка\n\nВторой\nабзац"
    assert details["Ремонт"] == "Хороший аккуратный"
