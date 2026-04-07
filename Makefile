.PHONY: runserver-prod

runserver-prod:
	ENV_FILE=.env.prod uv run python manage.py runserver
