import os
import tempfile

from vault import read_note


def test_reads_note_within_base():
    base = tempfile.mkdtemp()
    with open(os.path.join(base, "hello.txt"), "w", encoding="utf-8") as handle:
        handle.write("hi")
    assert read_note(base, "hello.txt") == "hi"
