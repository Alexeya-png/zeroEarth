from __future__ import annotations
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models.user import User
from db.repos.interfaces import IUserRepo

class SqlAlchemyUserRepo(IUserRepo):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_tg_id(self, tg_id: int) -> Optional[User]:
        res = await self._session.execute(select(User).where(User.tg_id == tg_id))
        return res.scalar_one_or_none()

    async def ensure(self, tg_id: int) -> User:
        user = await self.get_by_tg_id(tg_id)
        if user:
            return user
        user = User(tg_id=tg_id)
        self._session.add(user)
        await self._session.flush()
        return user
