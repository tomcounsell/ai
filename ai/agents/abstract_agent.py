from os import path
from fastai2.learner import load_learner
from abc import ABC

from apps.common.utilities.s3 import download_file_from_s3, download_s3_file_to_local


class Prediction(ABC):
    def __init__(self, prediction, _, probs):
        self.prediction, _, self.probabilities = prediction, _, probs

    def __repr__(self):
        return str(self.prediction)


class Agent(ABC):
    learner_filename = ""
    predictions = []

    def __init__(self):
        self.learn = self.get_learner()

    def get_learner(self):
        if not self.learn:
            local_learner_filename = f"temp/agent_{self.__class__.__name__}_model_file.pkl"
        try:
            path.exists(local_learner_filename)
        except:
            download_s3_file_to_local(self.learner_filename, local_learner_filename)
            self.learn = load_learner(local_learner_filename)
        return self.learn

    def predict(self, input) -> Prediction:
        prediction = Prediction(self.learn.predict(input))
        self.predictions.append(prediction)
        return prediction

    def get_confidence(self, num_predictions_ago: int = 1) -> float:
        if not len(self.predictions):
            raise Exception("no predictions made yet")
        return self.predictions[-num_predictions_ago].probabilities[1].item()
