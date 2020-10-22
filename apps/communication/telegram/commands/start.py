import logging
from apps.communication.telegram.commands.decorator import telegram_command
from telegram import Message

from apps.user.models import User


@telegram_command("start", response_type='text')
def start(telegram_bot_membership, message: Message, context):
    from apps.communication.telegram.commands.commands_index import feature_commands

    logging.debug(context.__dict__)
    if len(context.args) == 1:
        (username, four_digit_login_code) = context.args[0].split(":", 1)
        user = User.objects.get(username=username)
        if user.four_digit_login_code == four_digit_login_code:
            telegram_bot_membership.telegram_user_dict = str({k: v for k, v in message.from_user.__dict__.items() if not k.startswith('_')})
            telegram_bot_membership.save()

    return "\n".join([
        "This is the Telegram Bot for AI Helps",
        # "Follow @aihelps on [Twitter](https://twitter.com/aihelps) " +
        # "and [YouTube](https://youtube.com/channel/UCh7iHrTU8GIGGUOhTvCM3Dg) to learn how to build AI bots like this.",
        # "Or get an email for new blog posts via [Substack](https://aihelps.substack.com)",
        "",
        "Tap a command to do something:",
        ] + [
            "/%s - %s" % (command.execution_handle, command.help_text) for command in feature_commands
        ])

start.help_text = "get started"
