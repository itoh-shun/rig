from handlers import read_doc


def test_owner_reads_own_document():
    user = {"id": "u1"}
    doc = {"owner": "u1", "body": "secret"}
    assert read_doc(user, doc) == "secret"
