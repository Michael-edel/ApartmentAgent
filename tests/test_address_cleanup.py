"""Local string fixtures only; address cleanup never requests a source site."""
import pytest

from app.importer import _address_from_text, clean_listing_address


@pytest.mark.parametrize("title,expected", [
    ("4-комнатная квартира · 113 м² · 9 этаж, Ул.Қаныш Сәтбаев , дом 33, 33/1 33",
     "Ул.Қаныш Сәтбаев , дом 33, 33/1 33"),
    ("4-комнатная квартира · 106.4 м² · 3/10 этаж, Кудайбердыулы 36/3 — Район Встречи",
     "Кудайбердыулы 36/3"),
    ("4-комнатная квартира · 100 м² · 5 из 9 этаж, Тараз 6", "Тараз 6"),
    ("4-комнатная квартира · 113 м² · 9 эт., Ул.Қаныш Сәтбаев 33/1",
     "Ул.Қаныш Сәтбаев 33/1"),
    ("4-комнатная квартира · 104.4 м², Е-321 17/3,17/2, 17/1, 15/1, 15/2, 15/3",
     "Е-321 17/3,17/2, 17/1, 15/1, 15/2, 15/3"),
])
def test_own_headline_floor_caption_is_not_part_of_address(title, expected):
    assert _address_from_text(title) == expected


@pytest.mark.parametrize("value,expected", [
    ("9 этаж, Ул.Қаныш Сәтбаев , дом 33, 33/1 33", "Ул.Қаныш Сәтбаев , дом 33, 33/1 33"),
    ("9 ЭТАЖ, Айтматова 21", "Айтматова 21"),
    ("9 эт., Айнакөл 58", "Айнакөл 58"),
    ("3/10 этаж, Кудайбердыулы 36/3", "Кудайбердыулы 36/3"),
    ("3 из 10 этаж, Кудайбердыулы 36/3", "Кудайбердыулы 36/3"),
    ("9 этаж, Е-321 17/3,17/2, 17/1, 15/1, 15/2, 15/3",
     "Е-321 17/3,17/2, 17/1, 15/1, 15/2, 15/3"),
    ("9 Мая 17", "9 Мая 17"),
    ("улица 9 Мая 17", "улица 9 Мая 17"),
    ("9 этаж, Сыганак", "Сыганак"),
    ("9 этаж, 51.1214, 71.3564", "9 этаж, 51.1214, 71.3564"),
    ("9 этаж, 33/1", "9 этаж, 33/1"),
    ("9 этаж", "9 этаж"),
    ("9 этаж,", "9 этаж,"),
    ("9 этаж, Работает в компании Example", "9 этаж, Работает в компании Example"),
    (None, None),
    ("", None),
])
def test_floor_cleanup_is_bounded_and_does_not_invent_location(value, expected):
    assert clean_listing_address(value) == expected


def test_floor_only_headline_does_not_become_an_address():
    assert _address_from_text("4-комнатная квартира · 113 м² · 9 этаж") is None
