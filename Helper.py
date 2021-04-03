import json
import os


class Config:
    BOT_TOKEN = 'bot_token'
    DB_URL = 'db_url'
    PUBLIC_CHANNEL_NAME = 'public_channel_name'
    BOT_NAME = 'bot_name'
    BOT_PASSWORD = 'bot_password'
    THINGSPEAK_CHANNEL = 'thingspeak_channel'
    THINGSPEAK_READ_APIKEY = 'thingspeak_read_apikey'
    THINGSPEAK_FIELDS_ALARM_STATE_MAPPING = 'thingspeak_fields_alarm_state_mapping'

def loadConfig(fallback=None):
    try:
        return loadJson('config.json')
    except Exception as exc:
        print('Failed to load ' + 'config.json')
        return fallback

def loadJson(path):
    with open(os.path.join(os.getcwd(), path), encoding='utf-8') as infile:
        loadedJson = json.load(infile)
    return loadedJson

class SYMBOLS:
    BACK = '🔙'
    CONFIRM = '✅'
    DENY = '🚫'
    DENY2 = '❌'
    THUMBS_UP = '👍'
    THUMBS_DOWN = '👎'
    ARROW_RIGHT = '➡'
    ARROW_UP_RIGHT = '↗'
    ARROW_DOWN = '⬇'
    STAR = '⭐'
    HEART = '❤'
    BEER = '🍺'
    BEERS = '🍻'
    CORONA = '😷'
    FRIES = '🍟'
    INFORMATION = 'ℹ'
    WRENCH = '🔧'
    WARNING = '⚠'
    NEWSPAPER = '📰'
    PLUS = '➕'
    WHITE_DOWN_POINTING_BACKHAND = '👇'
