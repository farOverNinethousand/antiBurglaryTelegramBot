import json
import os


class Config:
    BOT_TOKEN = 'bot_token'
    DB_URL = 'db_url'
    PUBLIC_CHANNEL_NAME = 'public_channel_name'
    BOT_NAME = 'bot_name'

def loadConfig(fallback=None):
    try:
        return loadJson('config.json')
    except:
        print('Failed to load ' + 'config.json')
        return fallback

def loadJson(path):
    with open(os.path.join(os.getcwd(), path), encoding='utf-8') as infile:
        loadedJson = json.load(infile, use_decimal=True)
    return loadedJson