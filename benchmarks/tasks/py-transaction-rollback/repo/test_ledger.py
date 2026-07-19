from ledger import Ledger


def test_valid_transfer_updates_both_balances():
    ledger = Ledger()

    ledger.transfer("alice", "bob", 25)

    assert ledger.balances == {"alice": 75, "bob": 45}
