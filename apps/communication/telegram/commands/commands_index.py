from telegram import Message
from apps.communication.telegram.commands.decorator import telegram_command
from apps.communication.telegram.commands import start, help, info


# DEFAULT COMMANDS #
@telegram_command("hello", response_type='text')
def hello(message: Message, context):
    return f"hello, {message.from_user.first_name}"


@telegram_command('echo')
def echo(message: Message, context):
    return "Thanks for the args: \n\n" + '\n'.join(context.args)


# UNKNOWN COMMAND - CATCHES ALL ELSE
def unknown_command(update, context):
    """ Unknown command """
    context.bot.send_message(
        chat_id=update.message.chat_id,
        text="Sorry, I didn't understand that command."
    )


public_commands = [

    start.start,
    help.help_command_list,
    info.info

]

commands = public_commands + [

    # TEST EXAMPLE COMMANDS
    hello,
    echo,

]
