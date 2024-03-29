from apps.agent.fitness.abstract_motive import AbstractMotive


class Fear(AbstractMotive):
    """The amygdala represents a core fear system in the human body, which is involved in the expression of conditioned
    fear. Fear is measured by changes in autonomic activity including increased heart rate, increased blood pressure,
    as well as in simple reflexes such as flinching or blinking.
    refs: ventromedial prefrontal cortex
    """

    def __init__(self):
        super().__init__(zero=False)

    def cycle(self):
        self.change(-0.01)  # fear decays towards baseline (not goal)

    """Fear extinction is defined as a decline in conditioned fear responses (CRs) following nonreinforced exposure 
    to a feared conditioned stimulus (CS). ... However, there also is evidence to suggest that extinction is an 
    “unlearning” process corresponding to depotentiation of potentiated synapses within the amygdala. """


class RiskAppetite(AbstractMotive):
    """desire to take and avoid (aversion) risky situations in future and present"""

    def __init__(self):
        super().__init__(zero=True)

    def cycle(self):
        perceived_danger = 0.001  # todo
        self.change((perceived_danger - self.value) / self.value)


class SocialAdaptivity(AbstractMotive):
    """
    + desire for approval, appreciation, and acceptance by others
    + cooperation, desire to pursue social activities
    + social-confidence
    - fear of judgment and rejection
    - shyness, negative self-evaluation, self-consciousness,
    """

    def __init__(self):
        super().__init__(zero=False)


class Curiosity(AbstractMotive):
    """
    + desire for knowledge, attraction to the unknown, passion for learning
    + leads to exploratory behavior
    + avoids boredom
    + encourages actions to close gaps in understanding
    + encourages experimentation, poking the box, and measuring practical limits
    - averse to ambiguity
    refs: rostrolateral prefrontal cortex
    """

    def __init__(self):
        super().__init__(zero=True)


class Skepticism(AbstractMotive):
    """
    an attitude of doubt of the validity of knowledge (new or previously accepted)
    + reduced confidence that one's own judgements are accurate
    + resistance to new information that contradicts existing knowledge and wisdom (close-minded)
    - openness to accommodate new ideas as better than existing knowledge (brain plasticity?)
    """

    def __init__(self):
        super().__init__(zero=True)


class Obedience(AbstractMotive):
    """
    trait Conscientiousness: being careful, or diligent
    + submission and subjection to authority
    - opposition to authoritarianism
    - avoid being coerced
    - orneriness, disagreeableness (closely related to SocialAdaptivity)
    science: obeying orders has a measurable influence on how people perceive and process others’ pain (empathy)
    """

    def __init__(self):
        super().__init__(zero=False)


class Aggression(AbstractMotive):
    """
    Aggression is defines as behaviours that are intended to inflict harm (physical or emotional)
    Passive-aggressive behavior is characterized by passive hostility and an avoidance of direct communication
    In humans, aggressive behaviors evolved as adaptations to deal with competition, but
    when expressed out of context, they can have destructive consequences.
    science: a subset of hypothalamic and limbic brain areas tend to facilitate aggressive behaviour
    aggressive behaviour is regulated by serotonin neurotransmission
    """

    def __init__(self):
        super().__init__(zero=False)

    def cycle(self):
        self.goal = 0
