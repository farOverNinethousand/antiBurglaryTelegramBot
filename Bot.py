import logging
import time
from datetime import datetime, timedelta
from typing import Union

import couchdb
import schedule
from telegram import Update, ReplyMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Updater, ConversationHandler, CommandHandler, CallbackContext, CallbackQueryHandler, \
    MessageHandler, Filters

from Helper import Config, loadConfig, SYMBOLS
from hyper import HTTP20Connection  # we're using hyper instead of requests because of its' HTTP/2.0 capability
from json import loads
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


class CallbackVars:
    MENU_MAIN = 'MENU_MAIN'
    MENU_ASK_FOR_PASSWORD = 'MENU_ASK_FOR_PASSWORD'
    MUTE_HOURS_1 = 'MUTE_HOURS_1'
    MUTE_HOURS_12 = 'MUTE_HOURS_12'
    MUTE_HOURS_24 = 'MUTE_HOURS_24'
    MUTE_HOURS_48 = 'MUTE_HOURS_48'
    UNMUTE = 'UNMUTE'
    MUTE_SELECTION = 'MUTE_SELECTION'
    APPROVE_USER = 'APPROVE_USER'
    DECLINE_USER = 'DECLINE_USER'


class DATABASES:
    USERS = 'users'


class USERDB:
    USERNAME = 'username'
    FIRST_NAME = 'first_name'
    LAST_NAME = 'last_name'
    IS_APPROVED = 'is_approved'
    IS_ADMIN = 'is_admin'
    APPROVAL_REQUEST_HAS_BEEN_SENT = 'approval_request_has_been_sent'
    SNOOZE_UNTIL_TIMESTAMP = 'snooze_until_timestamp'


class ABBot:

    def __init__(self):
        self.cfg = loadConfig()
        if self.cfg is None or self.cfg.get(Config.DB_URL) is None:
            raise Exception('Broken config')
        """ Init DB """
        self.couchdb = couchdb.Server(self.cfg[Config.DB_URL])
        self.lastAlarmTimestamp = 0
        self.lastEntryID = -1
        """ Create required DBs """
        if DATABASES.USERS not in self.couchdb:
            self.couchdb.create(DATABASES.USERS)

        self.updater = Updater(self.cfg[Config.BOT_TOKEN], request_kwargs={"read_timeout": 30})
        dispatcher = self.updater.dispatcher
        # Main conversation handler - handles nearly all bot menus.
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.botDisplayMenuMain)],
            states={
                CallbackVars.MENU_MAIN: [
                    # Main menu
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MENU_MAIN + '$'),
                    CallbackQueryHandler(self.botUnsnooze, pattern='^' + CallbackVars.UNMUTE + '$'),
                ],
                CallbackVars.MENU_ASK_FOR_PASSWORD: [
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MENU_MAIN + '$'),
                    MessageHandler(Filters.text, self.botCheckPassword),
                ],
                CallbackVars.MUTE_SELECTION: [
                    CallbackQueryHandler(self.botSnooze, pattern='^' + CallbackVars.MUTE_HOURS_1 + '$'),
                    CallbackQueryHandler(self.botSnooze, pattern='^' + CallbackVars.MUTE_HOURS_12 + '$'),
                    CallbackQueryHandler(self.botSnooze, pattern='^' + CallbackVars.MUTE_HOURS_24 + '$'),
                    CallbackQueryHandler(self.botSnooze, pattern='^' + CallbackVars.MUTE_HOURS_48 + '$'),
                ]
            },
            fallbacks=[CommandHandler('start', self.botDisplayMenuMain)],
            name="MainConversationHandler",
        )
        dispatcher.add_handler(conv_handler)
        # conv_handler2 = ConversationHandler(
        #     entry_points=[CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.APPROVE_USER + '$')],
        #     states={
        #         CallbackVars.APPROVE_USER: [
        #             CommandHandler('cancel', self.botUserDeleteCancel),
        #             # Delete users account
        #             MessageHandler(Filters.text, self.botUserDelete),
        #         ],
        #
        #     },
        #     fallbacks=[CommandHandler('start', self.botDisplayMenuMain)],
        #     name="DeleteUserConvHandler",
        #     allow_reentry=True,
        # )
        # dispatcher.add_handler(conv_handler2)
        # dispatcher.add_error_handler(self.botErrorCallback)

    def isNewUser(self, userID: int) -> bool:
        if str(userID) in self.couchdb[DATABASES.USERS]:
            return False
        else:
            return True

    def isApprovedUser(self, userID: int) -> bool:
        userDoc = self.couchdb[DATABASES.USERS].get(str(userID))
        if userDoc is None or USERDB.IS_APPROVED not in userDoc:
            return False
        else:
            return userDoc[USERDB.IS_APPROVED]

    def botDisplayMenuMain(self, update: Update, context: CallbackContext):
        query = update.callback_query
        if query is not None:
            query.answer()
        if self.isNewUser(update.effective_user.id):
            menuText = 'Hallo ' + update.effective_user.first_name + ', <b>Passwort?</b>\n'
            self.botEditOrSendNewMessage(update, context, menuText)
            return CallbackVars.MENU_ASK_FOR_PASSWORD
        elif not self.isApprovedUser(update.effective_user.id):
            menuText = 'Warte auf Freischaltung durch einen Admin,\n'
            menuText += '\nDu wirst benachrichtigt, sobald dein Account freigeschaltet wurde.'
            self.botEditOrSendNewMessage(update, context, menuText)
            return CallbackVars.MENU_MAIN
        else:
            menuText = 'Hallo ' + update.effective_user.first_name + ','
            userDoc = self.couchdb[DATABASES.USERS][(str(update.effective_user.id))]
            if userDoc.get(USERDB.SNOOZE_UNTIL_TIMESTAMP, 0) > datetime.now().timestamp():
                # https://stackoverflow.com/questions/538666/format-timedelta-to-string
                secondsRemaining = userDoc[USERDB.SNOOZE_UNTIL_TIMESTAMP] - datetime.now().timestamp()
                duration = datetime.utcfromtimestamp(secondsRemaining)
                # print("Snoozed for seconds:" + str(secondsRemaining) + " | " + duration.strftime("%Hh:%Mm"))
                # print(timedelta(seconds=secondsRemaining))
                menuText += '\nBenachrichtigungen sind noch deaktiviert für: ' + duration.strftime("%Hh:%Mm")
                unmuteKeyboard = [
                    [InlineKeyboardButton('Benachrichtigungen aktivieren', callback_data=CallbackVars.UNMUTE)]]
                self.botEditOrSendNewMessage(update, context, menuText,
                                             reply_markup=InlineKeyboardMarkup(unmuteKeyboard))
                return CallbackVars.MENU_MAIN
            else:
                # Cleanup DB
                if USERDB.SNOOZE_UNTIL_TIMESTAMP in userDoc:
                    del userDoc[USERDB.SNOOZE_UNTIL_TIMESTAMP]
                    self.couchdb[DATABASES.USERS].save(userDoc)
                menuText += '\nHier kannst du Aktivitäten-Benachrichtigungen abschalten:'
                snoozeKeyboard = [
                    [InlineKeyboardButton('1 Stunde', callback_data=CallbackVars.MUTE_HOURS_1),
                     InlineKeyboardButton('12 Stunden', callback_data=CallbackVars.MUTE_HOURS_12)],
                    [InlineKeyboardButton('24 Stunden', callback_data=CallbackVars.MUTE_HOURS_24),
                     InlineKeyboardButton('48 Stunden', callback_data=CallbackVars.MUTE_HOURS_48)]
                ]
                self.botEditOrSendNewMessage(update, context, menuText,
                                             reply_markup=InlineKeyboardMarkup(snoozeKeyboard))
            return CallbackVars.MUTE_SELECTION

    def botSnooze(self, update: Update, context: CallbackContext):
        query = update.callback_query
        # query.answer()
        snoozeHours = int(query.data.replace("MUTE_HOURS_", ""))
        snoozeUntil = datetime.now().timestamp() + snoozeHours * 60 * 60
        userDoc = self.getUserDoc(update.effective_user.id)
        userDoc[USERDB.SNOOZE_UNTIL_TIMESTAMP] = snoozeUntil
        self.couchdb[DATABASES.USERS].save(userDoc)
        return self.botDisplayMenuMain(update, context)

    def botUnsnooze(self, update: Update, context: CallbackContext):
        userDoc = self.getUserDoc(update.effective_user.id)
        del userDoc[USERDB.SNOOZE_UNTIL_TIMESTAMP]
        self.couchdb[DATABASES.USERS].save(userDoc)
        return self.botDisplayMenuMain(update, context)

    def botCheckPassword(self, update: Update, context: CallbackContext):
        user_input = update.message.text
        if user_input == self.cfg[Config.BOT_PASSWORD]:
            # Update DB
            userData = {
                USERDB.USERNAME: update.effective_user.username,
                USERDB.FIRST_NAME: update.effective_user.first_name,
                USERDB.LAST_NAME: update.effective_user.last_name
            }
            text = SYMBOLS.CONFIRM + "Korrektes Passwort!"
            if len(self.couchdb[DATABASES.USERS]) == 0:
                # First user is admin
                userData[USERDB.IS_ADMIN] = True
                userData[USERDB.IS_APPROVED] = True
                text += "\n<b>Du bist Admin!</b>"
            self.couchdb[DATABASES.USERS][str(update.effective_user.id)] = userData
            context.bot.send_message(chat_id=update.effective_message.chat_id, text=text, parse_mode='HTML')
            return self.botDisplayMenuMain(update, context)
        else:
            context.bot.send_message(chat_id=update.effective_message.chat_id, text=SYMBOLS.DENY + "Falsches Passwort!",
                                     parse_mode='HTML')
            return CallbackVars.MENU_ASK_FOR_PASSWORD

    def botEditOrSendNewMessage(self, update: Update, context: CallbackContext, text: str,
                                reply_markup: ReplyMarkup = None):
        query = update.callback_query
        if query is not None:
            query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            context.bot.send_message(chat_id=update.effective_message.chat_id, reply_markup=reply_markup, text=text,
                                     parse_mode='HTML')

    def updateNotifications(self):
        usersToApprove = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if USERDB.IS_APPROVED not in userDoc and not userDoc.get(USERDB.APPROVAL_REQUEST_HAS_BEEN_SENT, False):
                usersToApprove[userID] = userDoc#
        adminUsers = self.getAdmins()
        if len(usersToApprove) > 0:
            logging.info("Sending out approval requests for users: " + str(len(usersToApprove)))
            index0 = 0
            for adminUserID in adminUsers:
                print("Sending approval requests to admin " + str((index0 + 1)) + " / " + str(len(adminUsers)))
                index1 = 0
                for userID, userDoc in usersToApprove.items():
                    print("Sending notification " + str((index1 + 1)) + " / " + str(len(usersToApprove)))
                    menuText = 'Benutzer erbittet Freischaltung: ' + self.getFullUsername(userID)
                    approvalKeyboard = [
                        [InlineKeyboardButton('Annehmen', callback_data=CallbackVars.APPROVE_USER),
                         InlineKeyboardButton('Ablehnen', callback_data=CallbackVars.DECLINE_USER)]
                    ]
                    reply_markup = InlineKeyboardMarkup(approvalKeyboard)
                    self.updater.bot.send_message(chat_id=adminUserID, reply_markup=reply_markup, text=menuText, parse_mode='HTML')
                    userDoc[USERDB.APPROVAL_REQUEST_HAS_BEEN_SENT] = True
                    userDB.save(userDoc)
                index0 += 1
        # https://community.thingspeak.com/documentation%20.../api/
        conn = HTTP20Connection('api.thingspeak.com')
        alarmMessages = ''
        conn.request("GET", '/channels/' + str(self.cfg[Config.THINGSPEAK_CHANNEL]) + '/feed.json?key=' + self.cfg[Config.THINGSPEAK_READ_APIKEY] + '&offset=1')
        apiResult = loads(conn.get_response().read())
        channelInfo = apiResult['channel']
        sensorResults = apiResult['feeds']
        currentLastEntryID = channelInfo['last_entry_id']
        fieldsAlarmStateMapping = self.cfg[Config.THINGSPEAK_FIELDS_ALARM_STATE_MAPPING]
        # E.g. no alarm on first start - we don't want to send alarms for old events or during testing
        if self.lastEntryID == -1:
            logging.info("Not checking for alarm because: First start")
            self.lastEntryID = currentLastEntryID
            return
        elif currentLastEntryID == self.lastEntryID:
            logging.info("Not checking for alarm because: last_entry_id hasn't changed - it still is: " + str(currentLastEntryID) + " --> No new data available")
            return
        # Most of all times we want to check only new entries but if e.g. the channel gets reset we need to check entries lower than our last saved number!
        checkOnlyHigherEntryIDs = True
        if currentLastEntryID < self.lastEntryID:
            checkOnlyHigherEntryIDs = False
            logging.info("Checking ALL entries")
        else:
            logging.info("Checking all entries > " + str(self.lastEntryID))
        # Let's find all fields an their names
        fieldsNameMapping = {}
        for key in channelInfo.keys():
            if key.startswith('field') and key[5:].isdecimal():
                fieldIDStr = key[5:]
                fieldsNameMapping[int(fieldIDStr)] = channelInfo[key]
        alarmDatetime = None
        alarmSensorsNames = []
        alarmSensorsTextStrings = []
        entryIDs = []
        for feed in sensorResults:
            # Check all fields for which we got alarm state mapping
            entryID = feed['entry_id']
            if entryID <= self.lastEntryID and checkOnlyHigherEntryIDs:
                # Ignore entries we've checked before
                continue
            for fieldIDStr in fieldsAlarmStateMapping.keys():
                fieldKey = 'field' + fieldIDStr
                if fieldKey not in feed:
                    logging.warning("Failed to find field: " + fieldKey)
                    continue
                currentFieldValue = int(feed[fieldKey])
                # Check if alarm state is given
                if currentFieldValue == fieldsAlarmStateMapping[fieldIDStr]:
                    thisDatetime = datetime.strptime(feed['created_at'], '%Y-%m-%dT%H:%M:%S%z')
                    fieldSensorName = fieldsNameMapping[int(fieldIDStr)]
                    # Only allow alarms every X minutes otherwise we'd send new messages every time this code gets executed!
                    if thisDatetime.timestamp() > (self.lastAlarmTimestamp + 1 * 60):
                        alarmDatetime = thisDatetime
                        if fieldSensorName not in alarmSensorsNames:
                            alarmSensorsNames.append(fieldSensorName)
                            alarmSensorsTextStrings.append(fieldSensorName + "(" + fieldKey + ")")
                        if entryID not in entryIDs:
                            entryIDs.append(entryID)
                        print("Triggered by entry: " + str(entryID))
                    else:
                        print("Ignoring alarm (flood protection): " + fieldSensorName)
        if len(alarmSensorsNames) > 0:
            alarmMessages += '\n' + alarmDatetime.strftime('%d.%m.%Y %H:%M Uhr') + ' | Sensoren: ' + ', '.join(alarmSensorsTextStrings)
            self.lastAlarmTimestamp = alarmDatetime.timestamp()
        if len(alarmMessages) > 0:
            approvedUsers = self.getApprovedUsers()
            logging.info("Sending out alarms to " + str(len(approvedUsers)) + " users...")
            text = "<b>Alarm! " + channelInfo['name'] + "</b>\n" + alarmMessages
            for userID in approvedUsers:
                userDoc = approvedUsers[userID]
                if userDoc.get(USERDB.IS_APPROVED, False):
                    try:
                        self.updater.bot.send_message(chat_id=userID, text=text, parse_mode='HTML')
                    except BadRequest:
                        # Maybe user has blocked bot
                        pass
        self.lastEntryID = currentLastEntryID

    def getFullUsername(self, userID) -> Union[str, None]:
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            return None
        if USERDB.USERNAME in userDoc:
            fullname = userDoc[USERDB.USERNAME]
        else:
            fullname = str(userID)
        fullname += "|" + userDoc[USERDB.FIRST_NAME]
        if USERDB.LAST_NAME in userDoc:
            fullname += "|" + userDoc[USERDB.LAST_NAME]
        return fullname

    def getAdmins(self) -> dict:
        admins = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN):
                admins[userID] = userDoc
        return admins

    def getApprovedUsers(self) -> dict:
        """ Returns approved users AND admins """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False):
                users[userID] = userDoc
        return users

    def getUserDoc(self, userID):
        return self.couchdb[DATABASES.USERS].get(str(userID))


if __name__ == '__main__':
    bot = ABBot()
    bot.updater.start_polling()
    schedule.every(5).seconds.do(bot.updateNotifications)
    while True:
        schedule.run_pending()
        time.sleep(1)
