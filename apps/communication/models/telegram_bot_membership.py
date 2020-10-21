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

    user = models.ForeignKey(AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="telegram_bot_memberships")
    # all memberships are for the same bot atm
    # bot_username = models.CharField(max_length=31, null=True, blank=True, help_text="eg. MyShopBot")

    telegram_user_id = models.BigIntegerField(unique=True, null=True)
    telegram_user_dict = models.JSONField(default=dict)

    expectations_list = models.JSONField(default=list)

    @property
    def telegram_user(self):
        return telegram.User(**self.telegram_user_dict)

    @property
    def telegram_bot(self):
        return self.telegram_user.bot


    # MODEL FUNCTIONS
    def respond_to(self, update: Update, context: CallbackContext):

        if len(self.expectations_list):
            from apps.communication.telegram.commands.commands_index import expectation_handlers
            for expectation_name in self.expectations_list[::-1]:
                if expectation_name in expectation_handlers:
                    logging.debug(f"attempting to run: {expectation_handlers[expectation_name].__name__}")
                    try:
                        response = expectation_handlers[expectation_name](update, context)
                    except Exception as e:
                        logging.debug(f"got exception: {str(e)}")
                    else:
                        logging.debug(f"got response: {response}")
                        self.expectations_list.remove(expectation_name)
                        self.save()
                        return response

        if update.message.video and isinstance(update.message.video, telegram.Video):
            logging.debug(update.message.video.__dict__)
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
