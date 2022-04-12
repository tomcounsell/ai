#!/usr/bin/env python
import os
import sys

from aihelps.scripts.dog_breeds import get_breed_from_filename  # todo: refactor this import

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
