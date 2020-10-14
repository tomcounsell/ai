""" Commands:

dog_breed - upload a dog photo and find out which dog breed it is
"""
import telegram
from telegram import Message, Update, File
from telegram.ext import CallbackContext
from telegram import Message

from ai.agents.dog_breeds import DogBreedsAgent
from apps.communication.models import TelegramBotMembership
from apps.communication.telegram.commands.decorator import telegram_command


def handle_photo_upload(update: Update, context: CallbackContext):
    if update.message.photo and isinstance(update.message.photo, telegram.PhotoSize):
        dog_breeds_agent = DogBreedsAgent()

        telegram_file = update.message.photo.get_file()
        image_local_path = telegram_file.download()
        breed = dog_breeds_agent.name_breed_from_image_local_path(image_local_path)
        confidence = dog_breeds_agent.get_confidence()
        return f"{confidence:.2f}% confident this is a {breed}"


@telegram_command("dog_breed", response_type='text')
def dog_breed(telegram_bot_membership, message: Message, context):
    telegram_bot_membership.expectation_list.append('photo_for_dog_breed')
    telegram_bot_membership.save()
    return f"Upload a photo."

dog_breed.help_text = "upload a dog photo and find out which dog breed it is"
