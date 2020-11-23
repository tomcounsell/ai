import logging
from io import BytesIO
from PIL import Image
import requests
from markdownify import markdownify

from settings import BING_SUBSCRIPTION_KEY


class Bing:
    api_host = "https://api.bing.microsoft.com/"
    headers = {"Ocp-Apim-Subscription-Key": BING_SUBSCRIPTION_KEY}
    search_results = dict()

    def test(self):
        response = requests.get(self.api_host)
        data = response.json()

    def search(self, search_terms, type='webpages'):
        endpoint = "/v7.0/search"
        if type == 'images':
            endpoint += "/#Images"
        elif type == 'videos':
            endpoint += "/#Videos"
        params = {"q": search_terms, "textDecorations": True, "textFormat": "HTML"}
        response = requests.get(self.api_host + endpoint, headers=self.headers, params=params)
        data = response.json()
        self.search_results[search_terms] = data

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

    def get_images(self, search_terms):
        if search_terms not in self.search_results or 'images' not in self.search_results.get(search_terms):
            self.search(search_terms, "images")
        image_search_results = self.search_results.get(search_terms)['images']['value']

        return [
            {
                'name': image_result['name'],
                'thumbnail_url': image_result['thumbnailUrl'],
                'image_url': image_result['contentUrl'],
            }
            for image_result in image_search_results
        ]
