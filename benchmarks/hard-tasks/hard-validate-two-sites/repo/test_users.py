import users
from users import create_user, import_users, all_users


def test_benign_users_are_created_via_both_paths():
    users._users.clear()
    create_user("alice")
    import_users(["bob", "carol"])
    assert all_users() == ["alice", "bob", "carol"]
