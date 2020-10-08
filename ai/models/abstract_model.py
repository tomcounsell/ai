from abc import ABC
from fastai2.learner import load_learner


class Prediction(ABC):
    def __init__(self, prediction, _, probs):
        self.prediction, _, self.probabilities = prediction, _, probs

    def __repr__(self):
        return str(self.prediction)


class AIModel(ABC):
    model_file = ""
    predictions = []

    def __init__(self, model_file=model_file):
        self.learn = load_learner(model_file)

    def predict(self, input) -> Prediction:
        prediction = Prediction(self.learn.predict(input))
        self.predictions.append(prediction)
        return prediction

    def get_last_confidence(self) -> float:
        if not len(self.predictions):
            raise Exception("no predictions made yet")
        return self.predictions[-1].probabilities[1].item()
