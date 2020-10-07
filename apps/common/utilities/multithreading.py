# A DECORATOR FOR PYTHON THREADING
# http://docs.python.org/2/library/threading.html#thread-objects
# http://stackoverflow.com/questions/18420699/multithreading-for-python-django
from threading import Thread

def start_new_thread(function):
    """
    if running any database transactions,
    include this at the end of your function
        ```
        from django.db import connection
        connection.close()
        ```
    """
    def decorator(*args, **kwargs):
        t = Thread(target = function, args=args, kwargs=kwargs)
        t.daemon = True
        t.start()
    return decorator


def run_all_multithreaded(function_def, list_of_params):
    """
    :param function_def: the function to pass params to
    :param list_of_params: list of function params, for multiple-param functions, pass tuples
    :return:
    """

    from multiprocessing.dummy import Pool as ThreadPool

    # make the Pool of workers
    pool = ThreadPool(16)

    # open functions in their own threads
    # and compile all the results
    results = pool.map(function_def, list_of_params)

    # close the pool and wait for the work to finish
    pool.close()
    pool.join()

    return results
