"""
Environment settings and configurations.
Handles loading environment variables and determining deployment type.
"""

import os
import socket
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent
SITE_ROOT = BASE_DIR

# Determine the correct .env file based on environment
env_file = ".env.local"
env_path = BASE_DIR / env_file
load_dotenv(env_path)

# Define environment type
PRODUCTION = STAGE = DEMO = LOCAL = False
dt_key = os.environ.get("DEPLOYMENT_TYPE", "LOCAL")

if dt_key == "PRODUCTION":
    PRODUCTION = True
elif dt_key == "DEMO":
    DEMO = True
elif dt_key == "STAGE":
    STAGE = True
else:
    LOCAL = True

# Debug mode based on environment
DEBUG = LOCAL or STAGE or (os.environ.get("DEBUG", "False").lower() == "true")

# Important security settings
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY and (LOCAL or DEBUG):
    SECRET_KEY = "django-insecure-key-for-development-only"

# Hosts configuration
ALLOWED_HOSTS = [
    # '.mycompany.com',
    # '.herokuapp.com',
    # '.amazonaws.com',
    "localhost",
    "127.0.0.1",
]

if LOCAL:
    CORS_ORIGIN_ALLOW_ALL = True
else:
    CORS_ORIGIN_WHITELIST = [
        # 'https://myproject-api*.herokuapp.com',
        # 'https://*.mycompany.com',
        # 'https://s3.amazonaws.com',
        # 'https://vendor_api.com',
        "https://localhost",
        "https://127.0.0.1",
    ]

# Hostname configuration
if PRODUCTION:
    HOSTNAME = "app.mycompany.com"
elif STAGE:
    HOSTNAME = "stage.mycompany.com"
else:
    try:
        HOSTNAME = socket.gethostname()
    except Exception:  # More specific exceptions are better
        HOSTNAME = "localhost"

# Internal IPs for debug toolbar
INTERNAL_IPS = [
    "127.0.0.1",
]
