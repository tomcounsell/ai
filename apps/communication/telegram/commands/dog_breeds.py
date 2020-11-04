""" Commands:

dog_breed - upload a dog photo and find out which dog breed it is
"""
import logging

import telegram
from telegram import Message, Update, File, PhotoSize
from telegram.ext import CallbackContext

from aihelps.skills.dog_breeds import DogBreedsSkill
from apps.communication.models import TelegramBotMembership
from apps.communication.telegram.commands.decorator import telegram_command


def handle_photo_upload(update: Update, context: CallbackContext):

    if not update.message.photo:
        logging.debug(update.message)
        raise Exception("expecting message.photo")
    photo = update.message.photo[0]
    if not isinstance(photo, PhotoSize):
        return "problem with this photo"

    dog_breeds_skill = DogBreedsSkill()
    if isinstance(update.message.photo, list) and len(update.message.photo) > 0:
        telegram_file = update.message.photo[0].get_file()
    else:
        telegram_file = update.message.photo.get_file()
    image_local_path = telegram_file.download()
    breed = dog_breeds_skill.name_breed_from_image_local_path(image_local_path)
    confidence = dog_breeds_skill.get_confidence()
    logging.debug(f"{confidence} of {breed}")
    return f"{100*confidence:.0f}% confident this is a {breed}"


@telegram_command("dog_breed", response_type='text')
def dog_breed(telegram_bot_membership, message: Message, context):
    telegram_bot_membership.expectations_list.append('photo_for_dog_breed')
    telegram_bot_membership.save()
    return f"Upload a photo."

dog_breed.help_text = "upload a dog photo and find out which dog breed it is"
