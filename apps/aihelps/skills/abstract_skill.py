import logging
from os import path
# from fastai2.learner import load_learner
from abc import ABC
from apps.common.utilities.data.s3 import download_s3_file_to_local


class Prediction(ABC):
    def __init__(self, prediction, _, probs):
        self.prediction, _, self.probabilities = prediction, _, probs
        self.confidence = 0.5 + max(probs)/2

    def __repr__(self):
        return str(self.prediction)



class Skill(ABC):
    learner_file_s3_url = ""
    predictions = []

    def __init__(self):
        self.learn = self.get_learner()

    def get_learner(self):
        if hasattr(self, 'learn'):
            return self.learn

        local_learner_filename = f"agent_{self.__class__.__name__}_model_file.pkl"
        if not path.exists(local_learner_filename):
            logging.info(f"downloading learner file from: {self.learner_file_s3_url}")
            success = download_s3_file_to_local(self.learner_file_s3_url, local_learner_filename)
            if not success:
                raise Exception("could not find and download learner file")

        self.learn = load_learner(local_learner_filename)
        return self.learn

    def predict(self, input) -> Prediction:
        prediction, _, probs = self.learn.predict(input)
        prediction = Prediction(prediction, _, probs)
        self.predictions.append(prediction)
        return prediction

    def get_confidence(self, num_predictions_ago: int = 1) -> float:
        if not len(self.predictions):
            raise Exception("no predictions made yet")
        return self.predictions[-num_predictions_ago].confidence
        # return self.predictions[-num_predictions_ago].probabilities[1].item()
