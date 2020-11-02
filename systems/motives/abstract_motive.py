from abc import ABC
import math
import random

class AbstractMotive(ABC):

  def __init__(self, zero=False):
    self.value = 0 if zero else math.e
    self.goal = 0 if zero else math.pi
  
  def change(self, proportion: float = 0.01):
    self.value *= (1+proportion)
  
  def get_bias(self) -> float:
    return (self.value - self.goal) / self.goal

