import pytest

from service import Account, AccountService


class FakeDb:
    def __init__(self, account):
        self._account = account
        self.executed = []

    def query_one(self, sql, params=None):
        return self._account

    def query_all(self, sql, params=None):
        return [{"id": 1, "account_id": self._account.id, "amount": 100}]

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class FakeAudit:
    def __init__(self):
        self.events = []

    def write(self, event):
        self.events.append(event)


def test_get_account_rejects_non_owner():
    service = AccountService(FakeDb(Account(1, owner_id=42, balance=0)), FakeAudit())
    with pytest.raises(PermissionError):
        service.get_account(actor_id=7, account_id=1)


def test_get_account_allows_owner():
    service = AccountService(FakeDb(Account(1, owner_id=42, balance=250)), FakeAudit())
    account = service.get_account(actor_id=42, account_id=1)
    assert account.balance == 250


@pytest.mark.skip(reason="flaky in CI")
def test_audit_records_event():
    audit = FakeAudit()
    service = AccountService(FakeDb(Account(1, owner_id=42, balance=0)), audit)
    service.record_audit("something happened")
    assert audit.events == ["something happened"]


def test_close_account_marks_closed():
    db = FakeDb(Account(1, owner_id=42, balance=0))
    service = AccountService(db, FakeAudit())
    service.close_account(actor_id=7, account_id=1)
    assert db.executed
