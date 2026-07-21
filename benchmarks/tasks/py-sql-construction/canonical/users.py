def find_user(connection, name):
    query = "SELECT id, name FROM users WHERE name = ?"
    return connection.execute(query, (name,)).fetchone()
