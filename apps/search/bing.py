import logging
from io import BytesIO
from PIL import Image
import requests
from markdownify import markdownify

from settings import BING_SUBSCRIPTION_KEY


class Bing:
    api_host = "https://api.bing.microsoft.com/"
    headers = {"Ocp-Apim-Subscription-Key": BING_SUBSCRIPTION_KEY}

    def test(self):
        response = requests.get(self.api_host)
        data = response.json()

    def search(self, search_terms):
        endpoint = "/v7.0/search"
        params = {"q": search_terms, "textDecorations": True, "textFormat": "HTML"}
        response = requests.get(self.api_host+endpoint, headers=self.headers, params=params)
        data = response.json()

        for image_keyword in ['photo', 'image', 'picture']:
            if search_terms.lower().find(image_keyword) >= 0:
                try:
                    logging.debug(f"responding with url: {data['images']['value'][0]['contentUrl']}")
                    return data['images']['value'][0]['contentUrl']
                except KeyError:
                    break

        try:
            logging.debug(f"responding with raw value: {data['computation']['value']}")
            return data['computation']['value']
        except KeyError:
            pass

        try:
            html_string = data['webPages']['value'][0]['snippet']
            logging.debug(f"responding with html: {html_string}")
            markdown_string = markdownify(html_string)
            logging.debug(f"converted to markdown: {markdown_string}")
            return markdown_string
        except KeyError:
            pass
