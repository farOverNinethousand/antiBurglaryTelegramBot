import json
import os
from datetime import datetime

from telegram import InlineKeyboardMarkup


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
    INFORMATION = 'ℹ'
    WRENCH = '🔧'
    WARNING = '⚠'
    NEWSPAPER = '📰'
    PLUS = '➕'
    WHITE_DOWN_POINTING_BACKHAND = '👇'
    MEGAPHONE = '📣'
    FLASH = '⚡'


def getFormattedTimeDelta(futureTimestamp: float) -> str:
    """ Returns human readable duration until given future timestamp is reached """
    # https://stackoverflow.com/questions/538666/format-timedelta-to-string
    secondsRemaining = futureTimestamp - datetime.now().timestamp()
    duration = datetime.utcfromtimestamp(secondsRemaining)
    return duration.strftime("%Hh:%Mm")


def formatTimestampToGermanDateWithSeconds(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime('%d.%m.%Y %H:%M:%S Uhr')


def formatTimestampToGermanDate(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime('%d.%m.%Y %H:%M Uhr')


def formatDatetimeToGermanDate(date: datetime) -> str:
    return date.strftime('%d.%m.%Y %H:%M:%S Uhr')


class BotException(Exception):
    def __init__(self, errorMsg, replyMarkup=None):
        self.errorMsg = errorMsg
        self.replyMarkup = replyMarkup

    def getErrorMsg(self):
        return self.errorMsg

    def getReplyMarkup(self) -> InlineKeyboardMarkup:
        return self.replyMarkup
