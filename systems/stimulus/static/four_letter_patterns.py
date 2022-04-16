import random


def get_noisy_sequence_data(sequence_length=4, repetitions=50, total_count=1000, noise_ratio=0.5) -> list:
    all_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    all_chars_count = len(all_chars)
    sequence_count = total_count * (1-noise_ratio)
    sequence_unique_count = sequence_count / repetitions

    sequences = []
    new_sequence_start_i = 0

    while len(sequences) < sequence_count:
        if new_sequence_start_i >= len(all_chars-4):
            raise Exception("refactor this method. too many unique sequences requested.")
        new_sequence = all_chars[new_sequence_start_i:new_sequence_start_i+3]
        sequences += [new_sequence for i in range(repetitions)]
        new_sequence_start_i += 1

    while len(sequences) < total_count:
        sequences.append("".join([all_chars[random.randint(0, all_chars_count - 1)] for j in range(sequence_length)]))

    random.shuffle(sequences)
    return sequences
