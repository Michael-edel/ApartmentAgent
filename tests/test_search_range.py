from app.config import Settings
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.search_analysis import analyze_search_result, is_target_search_result


def test_room_count_is_a_minimum_for_search_and_saved_rating():
    query = dict(max_price=50_000_000, min_area=50, max_area=100, target_rooms=3)
    for count in (3, 4, 5):
        title = f'{count}-комнатная квартира 70 м² 30000000 ₸'
        assert is_target_search_result(title, None, **query)
        listing = ListingCreate(source_url='https://krisha.kz/a/show/1', title=title,
                                price_kzt=30_000_000, area_m2=70, rooms=count)
        assert assess_listing(listing, Settings(target_rooms=3)).score == 100
    assert not is_target_search_result('2-комнатная квартира 70 м² 30000000 ₸', None, **query)
    assert not is_target_search_result('Квартира 70 м² 30000000 ₸', None, **query)
    studio = ListingCreate(source_url='https://krisha.kz/a/show/2', title='Евродвушка',
                           price_kzt=30_000_000, area_m2=70, rooms=2, is_full_two_room=False)
    assert assess_listing(studio, Settings(target_rooms=2)).score == 65


def test_minimum_price_requires_exact_price_and_uses_inclusive_bounds():
    query = dict(max_price=50_000_000, min_price=30_000_000, min_area=50, max_area=100, target_rooms=2)
    prefix = '2-комнатная квартира 70 м² '
    for amount in (30_000_000, 50_000_000):
        assert is_target_search_result(prefix + str(amount) + ' ₸', None, **query)
    assert not is_target_search_result(prefix + '29999999 ₸', None, **query)
    assert not is_target_search_result(prefix + '50000001 ₸', None, **query)
    assert not is_target_search_result(prefix + 'от 35000000 ₸', None, **query)
    assert not is_target_search_result(prefix, None, **query)
    without_min = {**query, 'min_price': None}
    assert is_target_search_result(prefix + 'от 35000000 ₸', None, **without_min)
    assert is_target_search_result(prefix, None, **without_min)
    assert not is_target_search_result(prefix + 'от 55000000 ₸', None, **without_min)
    below = analyze_search_result(prefix + '29999999 ₸', None, **query)
    assert below['score'] == 65 and 'цена ниже установленного минимума' in below['reasons']
    listing = ListingCreate(source_url='https://krisha.kz/a/show/3', title='Квартира',
                            price_kzt=29_000_000, area_m2=70, rooms=2)
    assert assess_listing(listing, Settings(target_rooms=2, min_price_kzt=30_000_000,
                                           max_price_kzt=50_000_000)).score == 85
