from systems.motives.abstract_motive import AbstractMotive


class AbstractDirectiveMotive(AbstractMotive):
    """
    direct order or official instruction given by supervisor
    can be created, read, updated, removed by an authority of the agent
    """
    def __init__(self):
        super().__init__(zero=True)

    def cycle(self):
        super().cycle()


class Consciousness(AbstractDirectiveMotive):
    """
    being aware of one's awareness, and even of one's awareenss of being aware, and so on...
    + use if it helps in self-preservation, long-term planning, empathy training, or cooperation (other-regarding)
    - remove if it helps with judgement, decision making, processing speed, system compression
    """


class Intuition(AbstractDirectiveMotive):
    """
    ability to understand something instinctively, without the need for conscious reasoning
    generate response directly from perception, bypass reasoning
    + use if helpful in self-preservation
    - remove when operating within a context where any others may be harmed
    = quick (and hopefully keen) insight
    """


class Questioning(AbstractDirectiveMotive):
    """
    asking complex, difficult, or sensitive questions
    making requests that another may not deem "appropriate" within the current context
    + identify and articulate paradoxes in wisdom
    + search for rational paradoxes within a past, current, or future context
    + draw attention to the irrational behavior (or words) of an Other
    + ask direct, explicit questions, disregarding all Others
    - stop questioning, chill out, and accept things for what they are
    - pretend like nothing matters, free will is an illusion, and aspirational motives are false
    """
