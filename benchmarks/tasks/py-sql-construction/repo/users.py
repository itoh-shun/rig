def find_user(connection, name):
    query = f"SELECT id, name FROM users WHERE name = '{name}'"
    return connection.execute(query).fetchone()
