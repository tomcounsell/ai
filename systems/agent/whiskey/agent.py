from popoto import Model, Field, DictField, ListField, SortedField


class Agent(Model):
    perception_space = Field()  # Choice from ['vision', 'metronome', 'translators', ...]
    input_SDRs = DictField()
    output_SDRs = DictField()
    flections = ListField()
    utility_score = SortedField()

    # grammar - capable complexity of communication
    # vocabulary - communication limits nouns, verbs, adjectives




def calc_value_score():

    utility = agent.utility_score  # the value as voted by other agents
    cost = agent.cost  # the cost of operation = the count of flections
    # maybe need to normalize first
    return utility - cost
