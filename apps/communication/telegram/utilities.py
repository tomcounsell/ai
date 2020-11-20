from textwrap import dedent
from typing import Callable
from io import BytesIO
import requests
from PIL import Image
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from telegram import Update, TelegramObject, ParseMode, Message
from telegram.ext import CallbackContext
from apps.common.utilities.multithreading import start_new_thread
from apps.communication.models import TelegramBotMembership


class TelegramBotException(Exception):
    def __init__(self, user_message="", developer_message=""):
        self.user_message = user_message
        self.developer_message = developer_message


def handle_telegram_message(update: Update, context: CallbackContext):
    # get user membership
    if update.message:
        telegram_bot_membership, um_created = TelegramBotMembership.objects.get_or_create(
            telegram_user_id=str(update.message.from_user.id)
        )
        response = telegram_bot_membership.respond_to(update, context)
        if response:
            try:
                url_validator = URLValidator()
                val = url_validator(response)
            except ValidationError:
                pass
            else:
                update.message.reply_photo(photo=response)
                return

            update.message.reply_markdown(response)
            return

def send_photo(telegram_bot_membership, local_file_path):
    # see https://github.com/python-telegram-bot/python-telegram-bot/wiki/Code-snippets#post-an-image-file-from-disk
    telegram_bot_membership.telegram_bot.send_photo(chat_id=telegram_bot_membership.telegram_user.effective_chat_id,
                                                    photo=open(local_file_path, 'rb'))


def send_cute_puppy_photo(self, bot, chat_id, caption=""):
    doggy_response = requests.get(url="https://dog.ceo/api/breeds/image/random", params={})
    data = doggy_response.json()
    if data.get('status') == "success" and data.get('message', "").startswith("https://images.dog.ceo"):
        dog_photo_url = data['message']
        bot.send_photo(chat_id=chat_id, photo=dog_photo_url, caption=caption)
