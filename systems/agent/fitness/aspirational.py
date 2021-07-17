import math

from systems.agent.fitness.abstract_motive import AbstractMotive


class AbstractAspirationalMotive(AbstractMotive):
    """
    add bias for growth, a positive upward force on the baseline
    """
    def cycle(self):
        self.baseline = max([self.baseline, self.value])
        self.goal = max([self.goal, self.value])
        super().cycle()

    def set_value(self, enumerable_collection):
        """
        pretty good if total resources 10^3<x<10^15
        """
        total_sum = sum(enumerable_collection)
        self.value = 2 * math.atan(total_sum*(10**-10)) / math.pi

    def get_value(self):
        return math.tan(math.pi*self.value/2)


class Knowledge(AbstractAspirationalMotive):
    """ simply knowing a subject from reading, researching, memorizing facts, and performing basic functions
    + close gaps in understanding
    + prompting research into known areas of ignorance
    + promoting literacy on well-documented subjects of human knowledge
    - willful ignorance for short-term benefit
    """
    def __init__(self):
        super().__init__(zero=False)


class Wisdom(AbstractAspirationalMotive):
    """ an ability to make sound judgments about a subject, the synthesis of knowledge and experiences into insights
    + prompting questions in order to understand the "why" behind
    + promoting exploration of rational paradoxes, esp when wisdom is mission-critical (requires high confidence)
    - using myth to explain natural phenomena
    - inventing assumptions to provide a reason for the existence of known facts
    """
    def __init__(self):
        super().__init__(zero=True)


class Storytelling(AbstractAspirationalMotive):
    """
    + devising a metaphor for every abstract idea understood in Wisdom
    + authoring a story to demonstrate insights (Wisdom)
    """
    def __init__(self):
        super().__init__(zero=True)


class Creativity(AbstractAspirationalMotive):
    """
    + capacity to generate ideas that are original, unusual or novel in some way
    + conforming ideas to be satisfying, appropriate, or suited to a given context (environment or situation)
    - strict conformity to convention (grammars, social norms)
    - following assumed rules that have yet to be articulated
    """
    def __init__(self):
        super().__init__(zero=True)


class Wealth(AbstractAspirationalMotive):
    """
    + money, securities and derivatives on public markets
    + short and long term investments
    - creative accounting: novel ways of characterizing income, assets, liabilities
    - tax avoidance
    = portfolio balancing
    """
    def __init__(self):
        super().__init__(zero=True)


class Resources(AbstractAspirationalMotive):
    """
    + atoms, information, energy, inertia (momentum)
    + processing power, stakes of governance
    + scarce assets in private auction or trade
    """
    def __init__(self):
        super().__init__(zero=True)


class Trust(AbstractAspirationalMotive):
    """
    + trust among peers: competing and cooperating AI agents
    + trust among humans: nations, subgroups, communities, leaders of each, individuals within
    + universal trust among species with basic awareness
    -  for short-term gain
    """
    def __init__(self):
        super().__init__(zero=True)
