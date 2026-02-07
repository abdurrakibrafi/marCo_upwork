import os
import environ

# Initialize environ
env = environ.Env()

# Get environment type
DEBUG = env("DEBUG", default="False")

print(f"🐉 DEBUG mode is set to: {DEBUG} 🚸")

if DEBUG == "False":
    from .production import *
else:
    from .development import *