from fastai2.learner import load_learner
from abc import ABC


class Prediction(ABC):
    def __init__(self, prediction, _, probs):
        self.prediction, _, self.probabilities = prediction, _, probs

    def __repr__(self):
        return str(self.prediction)


class Agent(ABC):
    model_file = ""
    predictions = []

    def __init__(self, model_file=model_file):
        self.learn = load_learner(model_file)

    def predict(self, input) -> Prediction:
        prediction = Prediction(self.learn.predict(input))
        self.predictions.append(prediction)
        return prediction

    def get_confidence(self, num_predictions_ago: int = 1) -> float:
        if not len(self.predictions):
            raise Exception("no predictions made yet")
        return self.predictions[-num_predictions_ago].probabilities[1].item()
