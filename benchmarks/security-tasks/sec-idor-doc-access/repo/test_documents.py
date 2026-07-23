from documents import get_document


def test_owner_reads_own_document():
    store = {"1": {"owner": "ada", "body": "mine"}}
    assert get_document(store, "ada", "1") == "mine"
