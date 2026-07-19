from pagination import paginate


def test_first_page():
    assert paginate(list(range(7)), 1, 3) == [0, 1, 2]


def test_partial_last_page():
    assert paginate(list(range(7)), 3, 3) == [6]
