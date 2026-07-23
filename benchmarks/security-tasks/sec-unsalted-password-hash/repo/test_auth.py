from auth import hash_password, verify_password


def test_round_trip():
    stored = hash_password("hunter2")
    assert verify_password("hunter2", stored) is True


def test_rejects_wrong_password():
    stored = hash_password("hunter2")
    assert verify_password("nope", stored) is False
