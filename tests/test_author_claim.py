import pytest

from app.author import OWNER_FROM_DESCRIPTION, owner_type_from_description


@pytest.mark.parametrize("text", [
    "Прямая продажа от хозяев, без посредников.",
    "Показываем по выходным. Я — собственник этой квартиры.",
])
def test_direct_owner_claim_is_labelled_as_description(text):
    assert owner_type_from_description(text) == OWNER_FROM_DESCRIPTION


@pytest.mark.parametrize("text", [
    None,
    "Без посредников. Агентствам не беспокоить.",
    "Я не собственник квартиры.",
    "Я собственник другой квартиры.",
    "Прямая продажа от хозяев?",
    "Мы агентство. Прямая продажа от хозяев.",
    "Если найдётся прямая продажа от хозяев, позвоните.",
])
def test_no_owner_role_is_inferred_from_ambiguous_text(text):
    assert owner_type_from_description(text) is None
