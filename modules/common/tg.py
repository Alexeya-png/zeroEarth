from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message


LOG = logging.getLogger(__name__)


_DELETE_IGNORE_SUBSTRINGS = (
    "message to delete not found",
    "message can't be deleted",
    "message can't be deleted for everyone",
    "message identifier is not specified",
)


def _is_ignorable_delete_error(err: Exception) -> bool:
    s = str(err).lower()
    return any(x in s for x in _DELETE_IGNORE_SUBSTRINGS)


async def safe_delete(message: Message | None) -> bool:
    if not message:
        return False
    try:
        await message.delete()
        return True
    except TelegramBadRequest as e:
        if _is_ignorable_delete_error(e):
            return False
        LOG.debug("safe_delete failed: %s", e)
        return False
    except TelegramForbiddenError as e:
        LOG.debug("safe_delete forbidden: %s", e)
        return False
    except Exception:
        LOG.exception("safe_delete unexpected error")
        return False


async def safe_edit(call: CallbackQuery, text: str, reply_markup=None) -> None:
    msg = call.message
    if not msg:
        return

    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return

        await safe_delete(msg)

        try:
            await msg.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            LOG.exception("safe_edit fallback send failed")
        return
    except Exception:
        LOG.exception("safe_edit unexpected error")
        try:
            await msg.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            LOG.exception("safe_edit final fallback send failed")


def cb_last_int(data: str | None) -> int | None:
    if not data:
        return None
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        return None
    return int(parts[-1])
