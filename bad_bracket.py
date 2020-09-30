
# toms_player_rank_tuples_set = {
#     ('tom', 8), ('amps', 7), ('trey', 6),
#     ('dew', 5), ('tony', 5),
#     ('bua', 3), ('emily', 5), ('perry', 4), ('maddie', 2),
# }
# amps_player_rank_tuples_set = {
#     ('tom', 9), ('amps', 8), ('trey', 8),
#     ('emily', 7), ('dew', 6), ('bua', 6), ('tony', 4),
#     ('perry', 4), ('maddie', 4),
# }

import random
from itertools import combinations, permutations, product

player_rank_tuples_set = {
    ('tom', 9), ('amps', 8), ('trey', 7),
    ('emily', 5), ('dew', 5), ('bua', 4), ('tony', 4),
    ('perry', 3), ('maddie', 2),
}

player_rank_dict = dict(list(player_rank_tuples_set))
player_names = [name for name, rank in player_rank_tuples_set]

teams_with_ranks = [
    (p1, p2, sum([player_rank_dict[p1], player_rank_dict[p2]]))
    for (p1, p2) in set(combinations(player_names, 2))
]

matches = set(combinations(teams_with_ranks, 2))
# remove matches where both teams have a player on common
matches = set(filter(lambda m: all([m[0][0] not in m[1], m[0][1] not in m[1]]), matches))
matches_count_before = len(matches)

def fun_rating(match):
    team_1_rank, team_2_rank = match[0][2], match[1][2]
    return 10 - abs(team_1_rank-team_2_rank)

# filter out matches where fun < 7/10
matches = list(set(filter(lambda m: fun_rating(m) >= 7, matches)))
not_fun_matches_count = matches_count_before-len(matches)

# remove a match if a player has already played in 35 matches
play_counts = {name: 0 for name in player_names}
matches_to_play = []
for match in matches:
    if any([play_counts[match[i][j]] >= 35 for (i, j) in list(product([0,1], [0,1]))]):
        pass
    else:
        matches_to_play.append(match)
        for (i, j) in list(product([0,1], [0,1])):
            play_counts[match[i][j]] += 1

random.shuffle(matches)
removed_matches_count = len(matches) - len(matches_to_play)

print(f"{matches_count_before} different matches can be played between {', '.join(player_names)}. \n"
      f"{not_fun_matches_count} matches filtered out for having one-sided skill levels. \n"
      f"{removed_matches_count} matches filtered out to give players more equal play time")

print(f"\nNumber of games each person will play:")
print(", ".join([f"{k}: {v}" for k, v in play_counts.items()]))

print(f"\n{len(matches_to_play)} matches:")
for match in matches_to_play:
    print(f"{match[0][0]}+{match[0][1]} vs. {match[1][0]}+{match[1][1]}")
