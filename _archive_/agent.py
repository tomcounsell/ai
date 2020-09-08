
class Agent(object):

    def __init__(self):
        self.alive = True
        while (self.alive):
            main_cognitive_loop()
        save_state()
        broadcast_obituary()
        shut_down()

    def percieve(self, environment):
        check_introspective_alert_flags(self)
        update_environment()

    def decide():
        if not found_emergencies():
            understand_situation()
            load_saved_plans()
            map_possible_futures()
            calculate_success_probabilities()
            save_possible_plans()
            commit_to_immediate_actions()

    def act():
        actions = load_actions()
        action_tree = form_dependancy_tree(actions)
        execute_action_tree(action_tree)

    def execute_action_tree(action_tree):
        dependent_threads = []
        for dependent_branch in action_tree.branches:
            dependent_threads.append(new_thread(
                execute_action_tree(dependent_branch)
            ))
        if len(dependent_threads):
            while (not all([thread.finished for thread in dependent_threads])):
                wait()
        action_tree.action()
        return


    def new_thread(action):
        thread = Thread(action, finished=False)
        queue(thread)
        return thread


    def main_cognitive_loop():
        perceive(self, environment)
        decide()
        act()
