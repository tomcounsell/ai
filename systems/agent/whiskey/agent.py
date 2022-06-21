from popoto import Model, Field, DictField, ListField, SortedField


class Agent(Model):
    perception_space = Field()  # Choice from ['vision', 'metronome', 'translators', ...]
    input_SDRs = DictField()
    output_SDRs = DictField()
    flections = ListField()
    utility_score = SortedField()
