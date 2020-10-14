import telegram
import logging
from django.contrib.postgres.fields import JSONField
from django.db import models
from telegram import Message, Update
from telegram.ext import CallbackContext

from apps.common.behaviors import Timestampable
from apps.common.models import Upload
from settings import AUTH_USER_MODEL


class TelegramBotMembership(Timestampable, models.Model):

    # all memberships are for the same bt atm
    bot_username = models.CharField(max_length=31, null=True, blank=True, help_text="eg. MyShopBot")
    user = models.ForeignKey(AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_bot_memberships")
    telegram_user_id = models.BigIntegerField(unique=True, null=True)
    telegram_user_dict = models.JSONField(default=dict)


    @property
    def telegram_user(self):
        return telegram.User(**self.telegram_user_dict)

    @property
    def telegram_bot(self):
        return self.telegram_user.bot


    # MODEL FUNCTIONS
    def respond_to(self, update: Update, context: CallbackContext):


        if update.message.video and isinstance(update.message.video, telegram.Video):
            logging.debug(update.message.video.__dict__)
            from telegram import Video, Bot
            file = update.message.video.get_file()

            upload = Upload.objects.create(
                original=file.file_path,
                thumbnail=update.message.video.thumb
            )
            upload.meta_data = update.message.video.__dict__
            upload.save()

            return f"saved as upload {upload.id}"


        if update.message.text:
            return "🆗👍"

        from apps.communication.telegram.utilities import send_cute_puppy_photo
        send_cute_puppy_photo(self, update, context,
                              caption="actually, I didn't save that. Tom didn't finish building me. "
                                      "In the meantime, here's a cute puppy.")
        return
