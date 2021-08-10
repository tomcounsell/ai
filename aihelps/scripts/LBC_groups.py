import pandas as pd
import csv

class Group:

    def __init__(self):
        with open("LBC_group_history.csv") as file:
            self.group_history = pd.read_csv(file)
