from textwrap import dedent
from typing import Callable

import requests
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
    user_membership = TelegramBotMembership.objects.filter(
        telegram_user_id=str(update.message.from_user.id)
    ).first()
    # handle message within context of the user's membership
    if user_membership:
        response = user_membership.respond_to(update, context)
        if response and isinstance(response, str):
            update.message.reply_text(response)
    else:
        update.message.reply_text("\n".join([
            "Sorry. In order to save your data,",
            "please first connect your _company_ account at",
            "_site_link_",
        ]))

def send_photo(user):
    bot, chat_id = user.bot, user.effective_chat_id
    # see https://github.com/python-telegram-bot/python-telegram-bot/wiki/Code-snippets#post-an-image-file-from-disk
    bot.send_photo(chat_id=chat_id, photo=open('tests/test.png', 'rb'))


def send_cute_puppy_photo(self, update, context, caption=""):
    doggy_response = requests.get(url="https://dog.ceo/api/breeds/image/random", params={})
    data = doggy_response.json()
    if data.get('status') == "success" and data.get('message', "").startswith("https://images.dog.ceo"):
        dog_photo_url = data['message']
        context.bot.send_photo(chat_id=update.effective_chat.id, photo=dog_photo_url, caption=caption)
