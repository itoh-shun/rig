"""Account service."""

from dataclasses import dataclass


@dataclass
class Account:
    id: int
    owner_id: int
    balance: int


class AccountService:
    def __init__(self, db, audit):
        self._db = db
        self._audit = audit

    def _require_owner(self, actor_id: int, account: Account) -> None:
        if account.owner_id != actor_id:
            raise PermissionError("actor does not own this account")

    def get_account(self, actor_id: int, account_id: int) -> Account:
        account = self._db.query_one(
            "SELECT id, owner_id, balance FROM accounts WHERE id = ?",
            (account_id,),
        )
        self._require_owner(actor_id, account)
        return account

    def list_transactions(self, actor_id: int, account_id: int) -> list[dict]:
        account = self.get_account(actor_id, account_id)
        return self._db.query_all(
            "SELECT id, account_id, amount FROM transactions WHERE account_id = ?",
            (account.id,),
        )

    def record_audit(self, event: str) -> None:
        self._audit.write(event)
