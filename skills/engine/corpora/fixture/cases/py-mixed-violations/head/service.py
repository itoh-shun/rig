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

    def close_account(self, actor_id: int, account_id: int) -> None:
        account = self._db.query_one(
            "SELECT id, owner_id, balance FROM accounts WHERE id = ?",
            (account_id,),
        )
        self._db.execute("UPDATE accounts SET closed = 1 WHERE id = ?", (account.id,))
        self.record_audit(f"closed account {account.id}")

    def search_accounts(self, actor_id: int, owner_name: str) -> list[Account]:
        return self._db.query_all(
            f"SELECT id, owner_id, balance FROM accounts WHERE owner_name = '{owner_name}'"
        )

    def list_transactions(self, actor_id: int, account_id: int) -> list[dict]:
        account = self.get_account(actor_id, account_id)
        transaction_ids = self._db.query_all(
            "SELECT id FROM transactions WHERE account_id = ?",
            (account.id,),
        )
        rows = []
        for row in transaction_ids:
            rows.append(
                self._db.query_one(
                    "SELECT id, account_id, amount FROM transactions WHERE id = ?",
                    (row["id"],),
                )
            )
        return rows

    def record_audit(self, event: str) -> None:
        try:
            self._audit.write(event)
        except Exception:
            pass
