import logging
import requests
from aihelps.skills.abstract_skill import Skill
from aihelps.scripts.dog_breeds import DogBreedsNN


class DogBreedsSkill(Skill):
    learner_file_s3_url = "https://aihelps-production.s3.amazonaws.com/ML_model_exports/dog_breeds_old.pkl"
    learner_file_s3_url = "https://aihelps-production.s3.amazonaws.com/ML_model_exports/dog_breeds_nn_model_export_6_3.pkl"

    def name_breed_from_image_local_path(self, image_local_path: str) -> str:
        from fastai.vision.core import PILImage
        pil_image = PILImage.create(image_local_path)

        try:
            prediction = self.predict(pil_image)
        except Exception as e:
            logging.critical(str(e))
            return "not sure"
        else:
            return str(prediction)


    def name_breed_from_image_url(self, image_url: str) -> str:

        image_data = requests.get(image_url).content
        with open('temp.jpgORpng', 'wb') as handler:
            handler.write(image_data)

        return self.name_breed_from_image_local_path('temp.jpgORpng')
