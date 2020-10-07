release: python manage.py migrate
web: waitress-serve --port=$PORT settings.wsgi:application
# worker: REMAP_SIGTERM=SIGQUIT celery --app=taskapp worker --concurrency=1 --hostname=$DYNO@%h -Ofair --purge --without-heartbeat --without-gossip --loglevel=debug
# scheduler: celery --app=taskapp beat --max-interval=10 -S redbeat.RedBeatScheduler
