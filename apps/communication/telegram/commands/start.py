import logging

from apps.communication.models import TelegramBotMembership
from apps.communication.telegram.commands.decorator import telegram_command
from telegram import Message

from apps.user.models import User


@telegram_command("start")
def start(message: Message, context):

    logging.debug(context.__dict__)
    if len(context.args) == 1:
        (username, four_digit_login_code) = context.args[0].split(":", 1)
        user = User.objects.get(username=username)
        if user.four_digit_login_code == four_digit_login_code:
            tbm, tbm_created = TelegramBotMembership.objects.get_or_create(
                user=user,
                telegram_user_id=message.from_user.id
            )
            tbm.telegram_user_dict = str({k: v for k, v in message.from_user.__dict__.items() if not k.startswith('_')})
            tbm.save()

            return "\n".join([
                "Welcome to _company_ Bot!",
                "You are now connected to your _company_ account.",
                "try /help for a list of commands and examples.",
                "Also, you can just start uploading photos and videos now.",
            ])

    return "\n".join([
        "Welcome to _company_ Bot!",
        "You can do things at _site_link_ ",
        "and find the Telegram button to connect your _company_ account",
    ])

start.help_text = "get started"
