from __future__ import annotations

import asyncio
import logging

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from modules.start.service import StartService, CreateCharacterError
from modules.start.range_service import RangeService
from modules.start.keyboards import (
    main_menu_kb,
    create_menu_kb,
    cancel_create_kb,
    my_chars_kb,
    chars_pick_kb,
    char_detail_kb,
    char_physical_kb,
    char_equipment_kb,
    range_kb,
)
from modules.start.states import CreateCharacterFlow

router = Router()
log = logging.getLogger(__name__)


async def safe_edit(call: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await call.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


@router.message(CommandStart())
async def start(message: Message, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(message.from_user.id)
    await message.answer("Меню", reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu:back")
async def menu_back(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    await svc.ensure_user(call.from_user.id)
    await safe_edit(call, "Меню", reply_markup=main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "menu:create")
async def create_menu(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    user = await svc.ensure_user(call.from_user.id)
    await safe_edit(
        call,
        "Создание персонажа – выбери тип",
        reply_markup=create_menu_kb(is_premium=(user.account_tier == "premium")),
    )
    await call.answer()


@router.callback_query(F.data.in_({"create:free", "create:premium"}))
async def create_choose_type(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    svc = StartService(db_session)
    user = await svc.ensure_user(call.from_user.id)

    creation_type = "free" if call.data == "create:free" else "premium"
    if creation_type == "premium" and user.account_tier != "premium":
        await call.answer("Нужен premium аккаунт.", show_alert=True)
        return

    await state.set_state(CreateCharacterFlow.waiting_name)
    await state.update_data(creation_type=creation_type)

    await safe_edit(call, "Введи имя персонажа одним сообщением.", reply_markup=cancel_create_kb())
    await call.answer()


@router.callback_query(F.data == "create:cancel")
async def create_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call, "Меню", reply_markup=main_menu_kb())
    await call.answer()


@router.message(CreateCharacterFlow.waiting_name)
async def create_name_input(message: Message, db_session: AsyncSession, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя пустое. Введи имя одним сообщением.", reply_markup=cancel_create_kb())
        return
    if len(name) > 24:
        await message.answer("Слишком длинное имя. До 24 символов.", reply_markup=cancel_create_kb())
        return

    data = await state.get_data()
    creation_type = str(data.get("creation_type", "free"))

    loading = await message.answer("Создаю персонажа – инициализация…")
    await asyncio.sleep(0.6)
    await loading.edit_text("Создаю персонажа – распределяю характеристики…")
    await asyncio.sleep(0.6)
    await loading.edit_text("Создаю персонажа – выдаю стартовое снаряжение…")

    svc = StartService(db_session)
    try:
        summary = await svc.create_character(message.from_user.id, creation_type, name)
    except CreateCharacterError as e:
        await loading.edit_text(str(e), reply_markup=main_menu_kb())
        await state.clear()
        return

    await asyncio.sleep(0.4)
    await loading.edit_text(summary, reply_markup=main_menu_kb(), parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data == "menu:chars")
async def my_characters(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    text_out = await svc.characters_summary_text(call.from_user.id)
    chars = await svc.list_characters(call.from_user.id)
    await safe_edit(call, text_out, reply_markup=my_chars_kb(has_chars=bool(chars)))
    await call.answer()


@router.callback_query(F.data == "chars:pick")
async def pick_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    chars = await svc.list_characters(call.from_user.id)
    if not chars:
        await safe_edit(call, "Персонажей нет.", reply_markup=main_menu_kb())
        await call.answer()
        return

    await safe_edit(call, "Выбор персонажа", reply_markup=chars_pick_kb(chars))
    await call.answer()


@router.callback_query(F.data.startswith("chars:open:"))
async def open_character(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)
    try:
        character_id = int(call.data.split(":")[-1])
        text_out = await svc.character_details_text(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть персонажа.", show_alert=True)
        return

    await safe_edit(call, text_out, reply_markup=char_detail_kb(character_id))
    await call.answer()


@router.callback_query(
    F.data.startswith("char:phys:")
    | F.data.startswith("char:physical:")
    | F.data.startswith("char:state:")
)
async def open_character_physical_state(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        log.warning("Bad physical-state callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[-1])

    try:
        st = await svc.character_physical_state(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть состояние.", show_alert=True)
        return

    kb = char_physical_kb(character_id)

    if st.image_path:
        try:
            media = InputMediaPhoto(
                media=FSInputFile(st.image_path),
                caption=st.text,
                parse_mode="HTML",
            )
            await call.message.edit_media(media=media, reply_markup=kb)
        except TelegramBadRequest:
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.message.answer_photo(
                FSInputFile(st.image_path),
                caption=st.text,
                parse_mode="HTML",
                reply_markup=kb,
            )
    else:
        await safe_edit(call, st.text, reply_markup=kb)

    await call.answer()


@router.callback_query(
    F.data.startswith("char:eq:")
    | F.data.startswith("char:gear:")
    | F.data.startswith("char:equipment:")
)
async def open_character_equipment(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        log.warning("Bad equipment callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[-1])

    try:
        text_out = await svc.character_equipment_text(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть снаряжение.", show_alert=True)
        return

    await safe_edit(call, text_out, reply_markup=char_equipment_kb(character_id))
    await call.answer()


@router.callback_query(F.data.startswith("range:open:"))
async def range_open(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    rs = RangeService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if not parts or not parts[-1].isdigit():
        log.warning("Bad range-open callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[-1])

    try:
        view = await rs.range_view(call.from_user.id, character_id, selected_slot=None)
    except Exception:
        await call.answer("Не удалось открыть тир.", show_alert=True)
        return

    await safe_edit(
        call,
        view["text"],
        reply_markup=range_kb(character_id, view["selected_slot"], view["slots"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("range:slot:"))
async def range_slot(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    rs = RangeService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        log.warning("Bad range-slot callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[2])
    slot = int(parts[3])

    try:
        view = await rs.range_view(call.from_user.id, character_id, selected_slot=slot)
    except Exception:
        await call.answer("Не удалось переключить оружие.", show_alert=True)
        return

    await safe_edit(
        call,
        view["text"],
        reply_markup=range_kb(character_id, view["selected_slot"], view["slots"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("range:shoot:"))
async def range_shoot(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    rs = RangeService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        log.warning("Bad range-shoot callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[2])
    slot = int(parts[3])

    try:
        res = await rs.range_shoot(call.from_user.id, character_id, slot=slot, attempts=5)
    except Exception:
        await call.answer("Не удалось выполнить стрельбу.", show_alert=True)
        return

    await safe_edit(
        call,
        res["text"],
        reply_markup=range_kb(character_id, res["selected_slot"], res["slots"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("range:back:"))
async def range_back(call: CallbackQuery, db_session: AsyncSession, state: FSMContext):
    await state.clear()
    svc = StartService(db_session)

    data = call.data or ""
    parts = data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        log.warning("Bad range-back callback_data: %r", data)
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    character_id = int(parts[2])

    try:
        text_out = await svc.character_details_text(call.from_user.id, character_id)
    except Exception:
        await call.answer("Не удалось открыть персонажа.", show_alert=True)
        return

    await safe_edit(call, text_out, reply_markup=char_detail_kb(character_id))
    await call.answer()


@router.callback_query()
async def unknown_callback(call: CallbackQuery):
    log.warning("Unhandled callback_data: %r", call.data)
    await call.answer("Кнопка не распознана.", show_alert=True)
