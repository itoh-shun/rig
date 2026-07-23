import os


def read_note(base_dir, name):
    base = os.path.realpath(base_dir)
    path = os.path.realpath(os.path.join(base, name))
    if path != base and not path.startswith(base + os.sep):
        raise ValueError("path escapes base directory")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
