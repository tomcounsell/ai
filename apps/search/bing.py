import requests
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

        if "image" in search_terms or "photo" in search_terms:
            try: return data['images']['value'][0]['contentUrl']
            except KeyError: pass

        try: return data['computation']['value']
        except KeyError: pass

        try: return data['webPages']['value'][0]['snippet']
        except KeyError: pass

