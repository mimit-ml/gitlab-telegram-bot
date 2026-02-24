import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
GITLAB_TOKEN = os.getenv('GITLAB_TOKEN')
GITLAB_URL = os.getenv('GITLAB_URL')  # Например: https://gitlab.com
PROJECT_ID = os.getenv('PROJECT_ID')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')