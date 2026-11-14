import pytest

from app.search_quality import evaluate_search_quality, is_confirmed_realtor_duplicate, layout_evidence, phrase_evidence, point_in_polygon


def test_exact_polygon_inside_outside_boundary_and_approximate():
    polygon = [[51,71],[51,72],[52,72],[52,71]]
    assert point_in_polygon(51.5,71.5,polygon)
    assert point_in_polygon(51,71.5,polygon)
    assert not point_in_polygon(50,71.5,polygon)
    approximate = evaluate_search_quality({'latitude':51.5,'longitude':71.5,'approximate_location':True},{'polygon':polygon})
    assert approximate['unknown'] == ['polygon'] and not approximate['matches']
    assert not evaluate_search_quality({'latitude':51.5,'longitude':71.5,'approximate_location':True},{'polygon':polygon,'require_confirmed':True})['matches']
    with pytest.raises(ValueError): point_in_polygon(51,71,[[1,2],[3,4]])


def test_layout_and_phrase_evidence_is_conservative():
    assert layout_evidence({'property_details':{'Планировка':'Раздельная'}})['source'] == 'field'
    assert layout_evidence({'description':'Комнаты изолированные.'}) is None
    assert layout_evidence({'description':'Изолированные комнаты.'})['value'] == 'separate'
    assert layout_evidence({'description':'Не изолированные комнаты.'}) is None
    assert phrase_evidence({'description':'Рядом школа.'},'школа')['source'] == 'description'
    assert phrase_evidence({'description':'Рядом нет школы.'},'школы') is None
    assert phrase_evidence({'description':'Этаж не последний.'}, 'не последний')['source'] == 'description'


def test_verified_realtor_duplicate_requires_identity_and_platform():
    a={'seller_type':'Риелтор','seller_id':'123','source_url':'https://www.olx.kz/d/a'}
    b={'seller_id':'123','source_url':'https://olx.kz/d/b'}
    assert is_confirmed_realtor_duplicate(a,b,same_apartment_confirmed=True)
    assert not is_confirmed_realtor_duplicate(a,b,same_apartment_confirmed=False)
    assert not is_confirmed_realtor_duplicate(a,{**b,'seller_id':'124'},same_apartment_confirmed=True)
    assert not is_confirmed_realtor_duplicate(a,{**b,'source_url':'https://krisha.kz/a/show/1'},same_apartment_confirmed=True)


def test_filters_do_not_exclude_unknown_without_explicit_strict_mode():
    assert evaluate_search_quality({}, {'exclude_cash_only':True}) == {'matches':True,'reasons':[],'unknown':['cash_only']}
    assert not evaluate_search_quality({'is_pledged':True}, {'exclude_pledged':True})['matches']
    assert not evaluate_search_quality({'price_is_lower_bound':True}, {'exclude_lower_bound':True})['matches']
    assert not evaluate_search_quality({'district':'Нура'}, {'districts':['Есильский район']})['matches']
    assert evaluate_search_quality({'district':'Есильский район'}, {'districts':['Есиль']})['matches']


def test_conditions_quality_survives_profile_validation_and_reaches_filters():
    from types import SimpleNamespace
    from app.search_profiles import ProfileCriteria
    from app.search_agent import conditions_for_listing, conditions_for_result
    profile = ProfileCriteria.model_validate({'max_price_kzt':50000000, 'min_area_m2':80,
        'conditions':{'quality':{'districts':['Нура'], 'layout':'separate', 'phrase':'рядом школа'}}})
    quality = profile.conditions.model_dump()['quality']
    assert quality['districts'] == ['Нура']
    item = SimpleNamespace(title='Квартира', description='Рядом школа.', district='Нура',
        property_details={'Планировка':'Раздельная'}, latitude=None, longitude=None)
    assert conditions_for_listing(item, profile.conditions)['matches_hard']
    item.district = 'Есильский район'
    assert not conditions_for_listing(item, profile.conditions)['matches_hard']
    row = SimpleNamespace(title='Квартира от 30 млн', snippet='только наличные', district=None)
    assert not conditions_for_result(row, conditions={'quality':{'exclude_lower_bound':True}})['matches_hard']


def test_polygon_validation_rejects_invalid_and_nonfinite():
    from pydantic import ValidationError
    from app.search_conditions import SearchConditions
    with pytest.raises(ValidationError): SearchConditions.model_validate({'quality':{'polygon': [[1,2],[1,2],[1,2]]}})
    with pytest.raises(ValidationError): SearchConditions.model_validate({'quality':{'polygon': [[float('nan'),2],[3,4],[5,6]]}})


def test_radius_is_straight_line_and_excludes_approximate_coordinates():
    from app.search_quality import distance_m
    assert distance_m((51,71),(51,71)) == 0
    assert 1100 < distance_m((51,71),(51.01,71)) < 1130
    criteria = {'radius':{'latitude':51,'longitude':71,'max_m':1200}}
    assert evaluate_search_quality({'latitude':51.01,'longitude':71}, criteria)['matches']
    assert not evaluate_search_quality({'latitude':51.02,'longitude':71}, criteria)['matches']
    unknown = evaluate_search_quality({'latitude':51.01,'longitude':71,'approximate_location':True}, criteria)
    assert unknown['unknown'] == ['radius']
    assert not evaluate_search_quality({'latitude':51.01,'longitude':71,'approximate_location':True}, {**criteria, 'require_confirmed':True})['matches']


def test_features_are_independent_and_use_explicit_evidence():
    from app.search_quality import feature_evidence
    item = {'description':'Кухня-гостиная и отдельная гардеробная. Есть лоджия.'}
    criteria = {'features':['kitchen_living','closet','balcony']}
    result = evaluate_search_quality(item, criteria)
    assert result['matches'] and len(result['reasons']) == 3
    assert all(x['evidence']['source'] == 'description' for x in result['reasons'])
    assert feature_evidence({'description':'Без балкона.'}, 'balcony')['value'] is False
    assert feature_evidence({'description':'В соседней квартире есть балкон.'}, 'balcony') is None
    assert not evaluate_search_quality({'property_details':{'Балкон':'нет'}}, {'features':['balcony']})['matches']


def test_profile_roundtrips_radius_features_and_validates_bounds():
    from app.search_profiles import ProfileCriteria
    from pydantic import ValidationError
    criteria = {'max_price_kzt':50000000, 'min_area_m2':80, 'conditions':{'quality':{
        'radius':{'latitude':51.1,'longitude':71.4,'max_m':1500}, 'features':['balcony','closet']}}}
    quality = ProfileCriteria.model_validate(criteria).model_dump()['conditions']['quality']
    assert quality['radius']['max_m'] == 1500 and quality['features'] == ['balcony','closet']
    criteria['conditions']['quality']['radius']['max_m'] = 0
    with pytest.raises(ValidationError): ProfileCriteria.model_validate(criteria)
