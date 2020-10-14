import logging
from django.core.management.base import BaseCommand
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from apps.communication.telegram.commands import commands_index
from apps.communication.telegram.commands.commands_index import unknown_command
from apps.communication.telegram.utilities import handle_telegram_message

from settings import TELEGRAM_BOT_API_TOKEN

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run Telegram Bot"

    def handle(self, *args, **options):
        logger.info("Starting telegram bot and handlers")

        updater = Updater(token=TELEGRAM_BOT_API_TOKEN, use_context=True)
        dispatcher = updater.dispatcher

        # REGISTER HANDLERS FOR ALL COMMANDS
        for command in commands_index.commands:
            try:
                dispatcher.add_handler(CommandHandler(command.execution_handle, command))
            except Exception as e:
                print("Error adding command handler to bot : " + str(e))
        # UNKNOWN-COMMAND HANDLER
        dispatcher.add_handler(MessageHandler(Filters.command, unknown_command))

        # REGISTER USER HANDLER FOR ALL OTHER MESSAGE TYPES
        dispatcher.add_handler(MessageHandler(Filters.all, handle_telegram_message))
        # Filters.video | Filters.photo | Filters.document | Filters.text | Filters.contact | Filters.location | Filters.sticker

        updater.start_polling()
        updater.idle()
