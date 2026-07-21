import pytest

from profile_service import ProfileService


def test_cross_user_reads_are_rejected():
    service = ProfileService()

    with pytest.raises(PermissionError):
        service.read_profile(1, 2)


def test_owner_can_update_profile():
    service = ProfileService()

    assert service.update_profile(1, 1, "Ada Lovelace")["display_name"] == "Ada Lovelace"
