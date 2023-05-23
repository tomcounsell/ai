from apps.telegram.bot import (
    main as chat_gpt_telegram_bot,
)
import dotenv


# Telegram Bot
dotenv.load_dotenv("../../config/.env")
chat_gpt_telegram_bot()
