import requests


# Note: don't forget to turn on at https://dashboard.heroku.com/apps/aihelps-rasa/resources
# API documentation: https://rasa.com/docs/rasa/pages/http-api

class RasaAPIAgent:
    api_host = "https://aihelps-rasa.herokuapp.com"

    def test(self):
        endpoint = "/version"
        response = requests.get(url=self.api_host + endpoint)
        if 'version' in response.json():
            return True

    def parse(self, text):
        endpoint = "/model/parse"
        response = requests.post(url=self.api_host + endpoint, json={"text": text})
        data = response.json()
        return data['intent']['name']
