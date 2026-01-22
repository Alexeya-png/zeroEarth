from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from db.models.user import User

class IUserRepo(ABC):
    @abstractmethod
    async def get_by_tg_id(self, tg_id: int) -> Optional[User]:
        raise NotImplementedError

    @abstractmethod
    async def ensure(self, tg_id: int) -> User:
        raise NotImplementedError
