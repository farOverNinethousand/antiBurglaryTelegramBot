import logging
import time
import traceback
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

from Sensor import Sensor

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
    APPROVED_BY = 'approved_by'
    IS_ADMIN = 'is_admin'
    APPROVAL_REQUEST_HAS_BEEN_SENT = 'approval_request_has_been_sent'
    SNOOZE_UNTIL_TIMESTAMP = 'snooze_until_timestamp'
    TIMESTAMP_REGISTERED = 'timestamp_registered'
    TIMESTAMP_LAST_SNOOZE = 'timestamp_last_snooze'
    TIMESTAMP_LAST_PASSWORD_TRY = 'timestamp_last_password_try'
    TIMESTAMP_LAST_APPROVAL_REQUEST = 'timestamp_last_approval_request'


class ABBot:

    def __init__(self):
        self.cfg = loadConfig()
        if self.cfg is None or self.cfg.get(Config.DB_URL) is None:
            raise Exception('Broken config')
        """ Init DB """
        self.couchdb = couchdb.Server(self.cfg[Config.DB_URL])
        self.lastAlarmSentTimestamp = -1
        self.lastEntryID = -1
        self.lastEntryIDChangeTimestamp = -1
        self.lastSensorUpdateDatetime = datetime.now()
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
        conv_handler2 = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.botApprovalAllow, pattern='^' + CallbackVars.APPROVE_USER + '.+$'), CallbackQueryHandler(self.botApprovalDeny, pattern='^' + CallbackVars.DECLINE_USER + '.+$')],
            states={
                CallbackVars.APPROVE_USER: [
                    CommandHandler('cancel', self.botDisplayMenuMain),
                    # Delete users account
                    MessageHandler(Filters.text, self.botDisplayMenuMain),
                ],
            },
            fallbacks=[CommandHandler('start', self.botDisplayMenuMain)],
            name="UserApprovalHandler",
            # allow_reentry=True,
        )
        dispatcher.add_handler(conv_handler2)
        # dispatcher.add_error_handler(self.botErrorCallback)

    def isNewUser(self, userID: int) -> bool:
        if str(userID) in self.couchdb[DATABASES.USERS]:
            return False
        else:
            return True

    def isApprovedUser(self, userID: int) -> bool:
        userDoc = self.getUserDoc(userID)
        if userDoc is None or USERDB.IS_APPROVED not in userDoc:
            return False
        else:
            return userDoc[USERDB.IS_APPROVED]

    def isAdmin(self, userID: int) -> bool:
        userDoc = self.getUserDoc(userID)
        if userDoc is None or USERDB.IS_ADMIN not in userDoc:
            return False
        else:
            return userDoc[USERDB.IS_ADMIN]

    def botDisplayMenuMain(self, update: Update, context: CallbackContext):
        query = update.callback_query
        if query is not None:
            query.answer()
        if self.isNewUser(update.effective_user.id):
            menuText = 'Hallo ' + update.effective_user.first_name + ', <b>Passwort?</b>\n'
            self.botEditOrSendNewMessage(update, context, menuText)
            return CallbackVars.MENU_ASK_FOR_PASSWORD
        # Known user -> Update DB as TG users could change their username and first/last name at any time!
        userDoc = self.getUserDoc(update.effective_user.id)
        userDoc[USERDB.FIRST_NAME] = update.effective_user.first_name
        if update.effective_user.username is not None:
            userDoc[USERDB.USERNAME] = update.effective_user.username
        if update.effective_user.last_name is not None:
            userDoc[USERDB.LAST_NAME] = update.effective_user.last_name
        if not self.isApprovedUser(update.effective_user.id):
            menuText = 'Warte auf Freischaltung durch einen Admin.'
            menuText += '\nDu wirst benachrichtigt, sobald dein Account freigeschaltet wurde.'
            userDoc[USERDB.TIMESTAMP_LAST_APPROVAL_REQUEST] = datetime.now().timestamp()
            self.couchdb[DATABASES.USERS].save(userDoc)
            self.botEditOrSendNewMessage(update, context, menuText)
            return CallbackVars.MENU_MAIN
        else:
            menuText = 'Hallo ' + update.effective_user.first_name + ','
            if userDoc.get(USERDB.SNOOZE_UNTIL_TIMESTAMP, 0) > datetime.now().timestamp():
                # https://stackoverflow.com/questions/538666/format-timedelta-to-string
                secondsRemaining = userDoc[USERDB.SNOOZE_UNTIL_TIMESTAMP] - datetime.now().timestamp()
                duration = datetime.utcfromtimestamp(secondsRemaining)
                # print("Snoozed for seconds:" + str(secondsRemaining) + " | " + duration.strftime("%Hh:%Mm"))
                # print(timedelta(seconds=secondsRemaining))
                menuText += '\nBenachrichtigungen für dich sind noch deaktiviert für: ' + duration.strftime("%Hh:%Mm")
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
                if self.isAdmin(update.effective_user.id):
                    menuText += '\n' + SYMBOLS.CONFIRM + '<b>Du bist Admin!</b>'
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
        userDoc[USERDB.TIMESTAMP_LAST_SNOOZE] = datetime.now().timestamp()
        self.couchdb[DATABASES.USERS].save(userDoc)
        return self.botDisplayMenuMain(update, context)

    def botUnsnooze(self, update: Update, context: CallbackContext):
        userDoc = self.getUserDoc(update.effective_user.id)
        del userDoc[USERDB.SNOOZE_UNTIL_TIMESTAMP]
        self.couchdb[DATABASES.USERS].save(userDoc)
        return self.botDisplayMenuMain(update, context)

    def botApprovalAllow(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        userIDStr = query.data.replace(CallbackVars.APPROVE_USER, "")
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None or userDoc.get(USERDB.IS_APPROVED, False):
            self.botEditOrSendNewMessage(update, context, SYMBOLS.DENY + "Anfrage bereits von anderem Admin bearbeitet")
        else:
            self.botEditOrSendNewMessage(update, context, SYMBOLS.CONFIRM + "Benutzer bestätigt: " + self.getMeaningfulUserTitle(userIDStr))
            userDoc[USERDB.IS_APPROVED] = True
            userDoc[USERDB.APPROVED_BY] = self.getMeaningfulUserTitle(update.effective_user.id)
            self.couchdb[DATABASES.USERS].save(userDoc)
            self.notifyUserApproved(int(userIDStr))
        return ConversationHandler.END

    def notifyUserApproved(self, userID: int):
        text = SYMBOLS.CONFIRM + "Du wurdest freigeschaltet!"
        text += "\nMit /start gelangst du in's Hauptmenü."
        self.updater.bot.send_message(chat_id=userID, text=text)

    def notifyUserDeny(self, userID: int):
        self.updater.bot.send_message(chat_id=userID, text=SYMBOLS.DENY + "Du wurdest abgelehnt!")

    def botApprovalDeny(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        userIDStr = query.data.replace(CallbackVars.DECLINE_USER, "")
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None:
            self.botEditOrSendNewMessage(update, context, SYMBOLS.DENY + "Anfrage bereits von anderem Admin bearbeitet")
        else:
            text = SYMBOLS.DENY + "Benutzer abgelehnt: " + self.getMeaningfulUserTitle(userIDStr)
            text += "\nVersehentlich abgelehnt? Mit dem Kommando /start kann der Benutzer eine neue anfrage stellen!"
            self.botEditOrSendNewMessage(update, context, text)
            del self.couchdb[DATABASES.USERS][userIDStr]
            self.notifyUserDeny(int(userIDStr))
        return ConversationHandler.END


    def userExistsInDB(self, userID) -> bool:
        return str(userID) in self.couchdb[DATABASES.USERS]

    def botCheckPassword(self, update: Update, context: CallbackContext):
        user_input = update.message.text
        if user_input == self.cfg[Config.BOT_PASSWORD]:
            # User entered correct password -> Add userdata to DB
            userData = {
                USERDB.FIRST_NAME: update.effective_user.first_name
            }
            if update.effective_user.username is not None:
                userData[USERDB.USERNAME] = update.effective_user.username
            if update.effective_user.last_name is not None:
                userData[USERDB.LAST_NAME] = update.effective_user.last_name
            userData[USERDB.TIMESTAMP_REGISTERED] = datetime.now().timestamp()
            text = SYMBOLS.CONFIRM + "Korrektes Passwort!"
            if len(self.couchdb[DATABASES.USERS]) == 0:
                # First user is admin
                userData[USERDB.IS_ADMIN] = True
                userData[USERDB.IS_APPROVED] = True
                text += "\n<b>Du bist der erste User -> Admin!</b>"
                self.couchdb[DATABASES.USERS][str(update.effective_user.id)] = userData
            else:
                self.couchdb[DATABASES.USERS][str(update.effective_user.id)] = userData
                self.sendUserApprovalRequestToAllAdmins(update.effective_user.id)
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

    def sendUserApprovalRequests(self):
        usersToApprove = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if USERDB.IS_APPROVED not in userDoc and not userDoc.get(USERDB.APPROVAL_REQUEST_HAS_BEEN_SENT, False):
                usersToApprove[userID] = userDoc
        if len(usersToApprove) > 0:
            logging.info("Sending out approval requests for users: " + str(len(usersToApprove)))
            index = 0
            for userID, userDoc in usersToApprove.items():
                print("Sending notification " + str((index + 1)) + " / " + str(len(usersToApprove)))
                self.sendUserApprovalRequestToAllAdmins(userID)

    def sendUserApprovalRequestToAllAdmins(self, userID):
        adminUsers = self.getAdmins()
        index = 0
        userDB = self.couchdb[DATABASES.USERS]
        userDoc = userDB[str(userID)]
        menuText = 'Benutzer erbittet Freischaltung: ' + self.getMeaningfulUserTitle(userID)
        approvalKeyboard = [
            [InlineKeyboardButton(SYMBOLS.CONFIRM + 'Annehmen', callback_data=CallbackVars.APPROVE_USER + str(userID)),
             InlineKeyboardButton(SYMBOLS.DENY + 'Ablehnen', callback_data=CallbackVars.DECLINE_USER + str(userID))]
        ]
        reply_markup = InlineKeyboardMarkup(approvalKeyboard)
        for adminUserID in adminUsers:
            print("Sending approval requests to admin " + str((index + 1)) + " / " + str(len(adminUsers)))
            try:
                self.updater.bot.send_message(chat_id=adminUserID, reply_markup=reply_markup, text=menuText, parse_mode='HTML')
            except BadRequest:
                # Ignore that
                logging.info("Failed to send approval request to admin: " + self.getMeaningfulUserTitle(adminUserID))
            index += 1
        userDoc[USERDB.APPROVAL_REQUEST_HAS_BEEN_SENT] = True
        userDB.save(userDoc)

    def updateNotifications(self):
        # https://community.thingspeak.com/documentation%20.../api/
        conn = HTTP20Connection('api.thingspeak.com')
        alarmMessages = ''
        conn.request("GET", '/channels/' + str(self.cfg[Config.THINGSPEAK_CHANNEL]) + '/feed.json?key=' + self.cfg[Config.THINGSPEAK_READ_APIKEY] + '&offset=1')
        apiResult = loads(conn.get_response().read())
        channelInfo = apiResult['channel']
        sensorResults = apiResult['feeds']
        currentLastEntryID = channelInfo['last_entry_id']
        # E.g. no alarm on first start - we don't want to send alarms for old events or during testing
        if self.lastEntryID == -1:
            logging.info("Not checking for alarm because: First start")
            self.lastEntryID = currentLastEntryID
            self.lastEntryIDChangeTimestamp = datetime.now().timestamp()
            return
        elif currentLastEntryID == self.lastEntryID and (datetime.now().timestamp() - self.lastEntryIDChangeTimestamp) >= 10 * 60:
            # Check if our alarm system maybe hasn't been responding for a long amount of time
            text = SYMBOLS.DENY + "<b>Fehler Alarmanlage!Keine neuen Daten verfügbar!\nLetzte Sensordaten vom: " + self.lastSensorUpdateDatetime.strftime('%d.%m.%Y %H:%M:%S Uhr') + "</b>\n" + alarmMessages
            self.sendMessageToAllApprovedUnmutedUsers(text)
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
        fieldIDToSensorMapping = {}
        for fieldIDStr, sensorUserConfig in self.cfg[Config.THINGSPEAK_FIELDS_ALARM_STATE_MAPPING].items():
            fieldKey = 'field' + fieldIDStr
            if fieldKey not in channelInfo:
                logging.info("Detected unconfigured field: " + fieldKey)
                continue
            fieldIDToSensorMapping[int(fieldIDStr)] = Sensor(name=channelInfo[fieldKey], triggerValue=sensorUserConfig['trigger'], triggerOperator=sensorUserConfig['operator'])
        alarmDatetime = None
        alarmSensorsNames = []
        alarmSensorsDebugTextStrings = []
        entryIDs = []
        for feed in sensorResults:
            # Check all fields for which we got alarm state mapping
            entryID = feed['entry_id']
            if entryID <= self.lastEntryID and checkOnlyHigherEntryIDs:
                # Ignore entries we've checked before
                continue
            for fieldID, sensor in fieldIDToSensorMapping.items():
                fieldKey = 'field' + str(fieldID)
                if fieldKey not in feed:
                    continue
                currentFieldValue = float(feed[fieldKey])
                sensor.setValue(currentFieldValue)
                thisDatetime = datetime.strptime(feed['created_at'], '%Y-%m-%dT%H:%M:%S%z')
                self.lastSensorUpdateDatetime = thisDatetime
                # Check if alarm state is given
                if sensor.isTriggered():
                    # Only allow alarms every X minutes otherwise we'd send new messages every time this code gets executed!
                    allowToSendAlarm = datetime.now().timestamp() > (self.lastAlarmSentTimestamp + 1 * 60)
                    if allowToSendAlarm:
                        alarmDatetime = thisDatetime
                        if sensor.getName() not in alarmSensorsNames:
                            alarmSensorsNames.append(sensor.getName())
                            alarmSensorsDebugTextStrings.append(sensor.getName() + "(" + fieldKey + ")")
                        if entryID not in entryIDs:
                            entryIDs.append(entryID)
                    else:
                        print("Flood protection: Ignoring alarm of sensor: " + sensor.getName())
        if len(alarmSensorsNames) > 0:
            logging.warning("Sending out alarms...")
            text = "<b>Alarm! " + channelInfo['name'] + "</b>"
            text += '\n' + alarmDatetime.strftime('%d.%m.%Y %H:%M:%S Uhr') + ' | Sensoren: ' + ', '.join(alarmSensorsNames)
            # Sending those alarms can take some time thus let's update this timestamp here already
            self.lastAlarmSentTimestamp = datetime.now().timestamp()
            self.sendMessageToAllApprovedUnmutedUsers(text)
        self.lastEntryID = currentLastEntryID
        self.lastEntryIDChangeTimestamp = datetime.now().timestamp()

    def sendMessageToAllApprovedUnmutedUsers(self, text: str):
        approvedUsers = self.getApprovedUnmutedUsers()
        logging.info("Sending messages to " + str(len(approvedUsers)) + " users...")
        for userID in approvedUsers:
            userDoc = approvedUsers[userID]
            if userDoc.get(USERDB.IS_APPROVED, False) and userDoc.get(USERDB.SNOOZE_UNTIL_TIMESTAMP, datetime.now().timestamp()) <= datetime.now().timestamp():
                try:
                    self.updater.bot.send_message(chat_id=userID, text=text, parse_mode='HTML')
                except BadRequest:
                    # Maybe user has blocked bot
                    pass

    def getMeaningfulUserTitle(self, userID) -> Union[str, None]:
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            return None
        if USERDB.USERNAME in userDoc:
            fullname = "@" + userDoc[USERDB.USERNAME]
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

    def getApprovedUnmutedUsers(self) -> dict:
        """ Returns approved users AND admins """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False) and userDoc.get(USERDB.SNOOZE_UNTIL_TIMESTAMP, datetime.now().timestamp()) <= datetime.now().timestamp():
                users[userID] = userDoc
        return users

    def getUserDoc(self, userID):
        return self.couchdb[DATABASES.USERS].get(str(userID))

    def handleBatchProcess(self):
        try:
            self.updateNotifications()
        except:
            traceback.print_exc()
            logging.warning("Batchprocess failed")


if __name__ == '__main__':
    bot = ABBot()
    bot.updater.start_polling()
    schedule.every(5).seconds.do(bot.handleBatchProcess)
    while True:
        schedule.run_pending()
        time.sleep(1)
