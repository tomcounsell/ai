from abc import ABC
import math

class AbstractMotive(ABC):

    def __init__(self, zero=False):
        self.value = 0 if zero else math.e
        self.baseline = 0 if zero else math.e
        self.goal = 0 if zero else math.pi
        self.arousal_threshold = 0.5

    def change(self, proportion: float = 0.01):
        # positive moves away from the baseline, negative moves towards the baseline
        self.value *= (1 + proportion) * abs(self.value - self.baseline)

    def cycle(self):
        if abs(self.value - self.baseline) > self.arousal_threshold:
            self.activate_arousal()
        return

    def activate_arousal(self):
        """being awoken or stimulated to a point of perception"""
        return

    def get_bias(self) -> float:
        return (self.value - self.goal) / self.goal

