import logging
import requests

from ai.agents.abstract_agent import Agent

class DogBreedsAgent(Agent):
    learner_filename = ""

    def name_breed_from_image_url(self, image_url: str) -> str:

        image_data = requests.get(image_url).content
        with open('temp.jpgORpng', 'wb') as handler:
            handler.write(image_data)

        from fastai2.vision.core import PILImage
        pil_image = PILImage.create('temp.jpgORpng')

        try:
            prediction = DogBreedsAgent.predict(pil_image)
        except Exception as e:
            logging.critical(str(e))
            return "not sure"
        else:
            return str(prediction)
