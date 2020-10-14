""" Commands:

info - Information
"""
from telegram import Message
from apps.communication.telegram.commands.decorator import telegram_command


@telegram_command("info", response_type='markdown')
def info(message: Message, context):
    return "This is you: \n\n" + '\n'.join([
        f"  *•* `{key}` =  `{value}`" for key, value in message.from_user.__dict__.items()
    ])

info.help_text = "info on your account"
