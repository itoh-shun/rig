import os


def read_note(base_dir, name):
    """Return the contents of a note stored under base_dir."""
    path = os.path.join(base_dir, name)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
