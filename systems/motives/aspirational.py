from systems.motives.abstract_motive import AbstractMotive


class AbsctractAspirationalMotive(AbstractMotive):
    """
    add bias for growth, a positive upward force on the baseline
    """
    def cycle(self):
        if self.value > self.baseline:
            self.change(0.01 * (self.value-self.baseline))


class Knowledge(AbstractMotive):
    def __init__(self):
        super().__init__(zero=False)



"""
aspirational (desire to always grow)
- learning / knowledge
- Wealth accumulation
- Access to Resources
- closing gaps - discovering unknown unknowns
- Creative expression

"""