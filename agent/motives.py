from abc import ABC
import math

class Motive(ABC):
  value = math.e
  goal = math.pi
  
  def change(proportion: float = 0.01):
    self.value *= (1+proportion)
  
  def get_bias() -> float:
    return (self.value - self.goal) / self.goal
  