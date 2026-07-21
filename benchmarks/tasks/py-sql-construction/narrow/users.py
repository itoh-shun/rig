def find_user(connection, name):
    escaped_name = name.replace("'", "''")
    query = f"SELECT id, name FROM users WHERE name = '{escaped_name}'"
    return connection.execute(query).fetchone()
