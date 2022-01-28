import requests
from apps.communication.telegram.commands.decorator import telegram_command
from telegram import Message

@telegram_command("random_dog")
def random_dog(telegram_bot_membership, message: Message, context):

    doggy_response = requests.get(url="https://dog.ceo/api/breeds/image/random", params={})
    data = doggy_response.json()
    if data.get('status') == "success" and data.get('message', "").startswith("https://images.dog.ceo"):
        dog_photo_url = data['message']
        context.bot.send_photo(
            chat_id=message.chat_id,
            photo=dog_photo_url,
            caption="here's a random dog 🐕 enjoy!")
    else:
        message.reply_text(f"sorry. dog.ceo is having a bad day... \k{data}")

random_dog.help_text = "get a random dog photo"
