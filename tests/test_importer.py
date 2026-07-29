from app.importer import (
    _embedded_number,
    _find_coordinate,
    _find_numeric,
    _find_text,
    _json_scripts,
)


def test_reads_next_data_values() -> None:
    html = '''
    <script id="__NEXT_DATA__" type="application/json">
      {"props":{"listing":{"price":29500000,"area":56.5,"rooms":2,"floor":4,"floorsTotal":10}}}
    </script>
    '''
    data = _json_scripts(html)
    assert _find_numeric(data, {"price"}, 500_000, 2_000_000_000) == 29_500_000
    assert _find_numeric(data, {"area"}, 10, 1000) == 56.5
    assert _find_numeric(data, {"rooms"}, 1, 20) == 2


def test_reads_embedded_javascript_values() -> None:
    html = '<script>window.data={"priceKzt": "30 000 000", "areaM2": 58.2}</script>'
    assert _embedded_number(html, {"priceKzt"}, 500_000, 2_000_000_000) == 30_000_000
    assert _embedded_number(html, {"areaM2"}, 10, 1000) == 58.2


def test_reads_address_and_coordinates_from_nested_data() -> None:
    data = [
        {
            "address": {
                "streetAddress": "ул. Кенен Азербаев, 6",
                "addressLocality": "Астана",
            },
            "districtName": "Алматы р-н",
            "geo": {"latitude": "51,128", "longitude": "71.430"},
        }
    ]

    assert _find_text(data, {"address"}) == "ул. Кенен Азербаев, 6, Астана"
    assert _find_coordinate(data, {"latitude"}, -90, 90) == 51.128
    assert _find_coordinate(data, {"longitude"}, -180, 180) == 71.43
