def build_message(name, prefix="Hello"):
    return f"{prefix}, {name}!"


def format_message(name, prefix="Hello"):
    return build_message(name, prefix)
