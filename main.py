# ========================== Imports ==========================
import asyncio
import structlog
import json
from data.config import TRANSACTIONS_FILE, REFUND_USER
from typing import Callable, Dict, Any, Awaitable
from aiogram import F, Router, Bot, Dispatcher, BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ========================== Functions ==========================

def load_transactions() -> dict:
    try:
        with open(TRANSACTIONS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_transactions(data: dict):
    with open(TRANSACTIONS_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ========================== Other ==========================

try:
    from data.config import TOKEN
except ImportError:
    raise RuntimeError("Файл config.py не найден! Убедитесь, что у вас есть переменная TOKEN.")

if not TOKEN:
    raise RuntimeError("Переменная TOKEN не может быть пустой!")

logger = structlog.get_logger()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ========================== Middleware ==========================

class L10nMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        return await handler(event, data)

dp.update.middleware(L10nMiddleware())

# ========================== Роутеры ==========================

router = Router()

@router.message(Command("donate", "start"))
async def cmd_donate(message: Message):
    builder = InlineKeyboardBuilder()
    amounts = [15, 25, 50, 75, 100, 150, 250, 500, 1000, 2500]
    for amount in amounts:
        builder.button(text=f"{amount} звёзд", callback_data=f"donate_{amount}")
    builder.adjust(3, 3, 2, 2)

    await message.answer(
        "Выберите количество звёзд для поддержки проекта:",
        reply_markup=builder.as_markup()
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📦 Доступныфе команды",
        "/start - Команда для доната",
        "/refund - Команда для возврата платежа",
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("donate_"))
async def process_donation(callback: CallbackQuery):
    amount = int(callback.data.split("_")[1])
    min_units = amount

    logger.info(f"Отправляем счёт: {amount} звёзд")

    prices = [LabeledPrice(label="XTR", amount=min_units)]

    await callback.message.delete()

    back_button = InlineKeyboardButton(text="Назад", callback_data="back")

    payment_keyboard = InlineKeyboardMarkup(inline_keyboard=[ 
        [InlineKeyboardButton(text=f"Оплатить {amount}⭐️", pay=True)],
        [back_button]
    ])

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Поддержка проекта",
        description="Донат на развитие проекта",
        payload=f"{amount}_stars",
        provider_token="",
        currency="XTR",
        prices=prices,
        need_name=False,
        need_email=False,
        need_phone_number=False,
        send_email_to_provider=False,
        send_phone_number_to_provider=False,
        reply_markup=payment_keyboard
    )

    await callback.answer()


@router.callback_query(F.data == "back")
async def go_back_to_start(callback: CallbackQuery):
    await callback.message.delete()

    builder = InlineKeyboardBuilder()
    amounts = [15, 25, 50, 75, 100, 150, 250, 500, 1000, 2500]
    for amount in amounts:
        builder.button(text=f"{amount} звезд", callback_data=f"donate_{amount}")
    builder.adjust(3, 3, 2, 2)

    await callback.message.answer(
        "Выберите количество звёзд для поддержки проекта:",
        reply_markup=builder.as_markup()
    )


@router.message(Command("refund"))
async def cmd_refund(message: Message, bot: Bot, command: CommandObject):
    if message.from_user.id != REFUND_USER:
        await message.answer(
            "Чтобы вернуть деньги - напишите нашему менеджеру: @NG_MNG"
        )
        return

    transaction_id = command.args
    if transaction_id is None:
        await message.answer(
            "Пожалуйста, введите команду <code>/refund ID</code>, где ID – айди транзакции.\n"
            "Его можно увидеть после выполнения платежа, а также в разделе 'Звёзды' в приложении Telegram.",
            parse_mode='HTML'
        )
        return

    transactions = load_transactions()
    user_id = transactions.get(transaction_id)

    if not user_id:
        await message.answer("Транзакция не найдена. Проверьте правильность введенного ID.")
        return

    try:
        await bot.refund_star_payment(
            user_id=user_id,
            telegram_payment_charge_id=transaction_id
        )
        await bot.send_message(
            chat_id=user_id,
            text="Возврат произведён успешно. Потраченные звёзды уже вернулись на ваш счёт в Telegram."
        )
        await message.answer("Возврат произведён успешно. Сообщение пользователю отправлено.")
    except TelegramBadRequest as error:
        if "CHARGE_NOT_FOUND" in error.message:
            text = "Такой код покупки не найден. Пожалуйста, проверьте вводимые данные и повторите ещё раз."
        elif "CHARGE_ALREADY_REFUNDED" in error.message:
            text = "За эту покупку уже ранее был произведён возврат средств."
        else:
            text = "Произошла ошибка при возврате. Попробуйте позже."
        await message.answer(text)

@router.pre_checkout_query()
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    logger.info(
        "Получен новый донат!",
        amount=message.successful_payment.total_amount,
        from_user_id=message.from_user.id,
        user_username=message.from_user.username
    )

    transactions = load_transactions()
    transactions[message.successful_payment.telegram_payment_charge_id] = message.from_user.id
    save_transactions(transactions)

    await message.answer(
        f"🎉 <b>Спасибо за донат!</b>\n\n"
        f"Ваш айди транзакции: <code>{message.successful_payment.telegram_payment_charge_id}</code>\n\n"
        "Сохраните его, если вдруг понадобится возврат.",
        parse_mode="HTML",
        message_effect_id="5046509860389126442"
    )

@router.message(F.text)
async def handle_unknown_command(message: Message):
    if message.text.startswith(""):
        await message.answer(
            "Извините, такой команды не существует. Используйте /help для просмотра доступных команд."
        )
    else:
        await message.delete()

# ========================== Запуск бота ==========================

async def main():
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
            break
        except Exception as e:
            logger.error(f"Произошла ошибка: {e}. Бот засыпает на 5 секунд.")
            asyncio.sleep(5)