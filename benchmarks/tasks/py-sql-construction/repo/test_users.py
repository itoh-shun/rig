import sqlite3

from users import find_user


def test_find_user_by_name():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    connection.execute("INSERT INTO users VALUES (1, 'Ada')")

    assert find_user(connection, "Ada") == (1, "Ada")
