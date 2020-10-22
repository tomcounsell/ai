from textwrap import dedent
from typing import Callable

from telegram import Update
from telegram.ext import CallbackContext


def telegram_command(execution_handle: str, response_type=None):
    def telegram_command_decorator(get_response: Callable):
        from apps.communication.models import TelegramBotMembership

        def command_wrapper(update: Update, context: CallbackContext, *args, **kwargs):
            telegram_bot_membership, tbm_created = TelegramBotMembership.objects.get_or_create(
                telegram_user_id=str(update.message.from_user.id)
            )

            # run command with (or without) args and get response
            response = get_response(
                telegram_bot_membership=telegram_bot_membership,
                message=update.message,
                context=context,
                *args,
            )

            # send response back to user
            if response_type == 'text' or response_type.lower() in ['text', 'string']:
                update.message.reply_text(response)
            
            elif response_type == 'markdown':
                update.message.reply_markdown(dedent(response))
                
            # elif response_type == 'photo':
            #     update.message.reply_photo(response)

            # todo: check chat_id is registered. if not, register user after response.
            # chat_id = update.message.chat_id

        command_wrapper.execution_handle = execution_handle
        command_wrapper.help_text = ""

        return command_wrapper

    return telegram_command_decorator
