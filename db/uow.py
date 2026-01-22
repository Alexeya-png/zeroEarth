from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession

from db.repos.user_repo import SqlAlchemyUserRepo

@dataclass
class UoW:
    session: AsyncSession

    @property
    def users(self) -> SqlAlchemyUserRepo:
        return SqlAlchemyUserRepo(self.session)
