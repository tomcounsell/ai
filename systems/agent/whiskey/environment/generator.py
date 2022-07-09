import random


def generate_environment():
    # List of possible environment types
    environment_types = ["forest", "jungle", "desert", "mountain", "swamp"]

    # Pick a random environment type
    environment_type = random.choice(environment_types)

    # List of possible environment hazards
    environment_hazards = {
        "forest": ["wild animals", "dangerous plants", "slippery ground"],
        "jungle": ["wild animals", "dangerous plants", "disease"],
        "desert": ["extreme heat", "lack of water", "dangerous animals"],
        "mountain": ["cold weather", "avalanches", "rock slides"],
        "swamp": ["dangerous animals", "quicksand", "narrow passages"],
    }

    # Pick a random environment hazard
    environment_hazard = random.choice(environment_hazards[environment_type])

    # Return the generated environment
    return environment_type, environment_hazard
