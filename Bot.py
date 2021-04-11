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

from Helper import Config, loadConfig, SYMBOLS, getFormattedTimeDelta, formatDatetimeToGermanDate, formatTimestampToGermanDateWithSeconds, formatTimestampToGermanDate, BotException
from hyper import HTTP20Connection  # we're using hyper instead of requests because of its' HTTP/2.0 capability
from json import loads

from Sensor import Sensor

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


class CallbackVars:
    MENU_MAIN = 'MENU_MAIN'
    MENU_ASK_FOR_PASSWORD = 'MENU_ASK_FOR_PASSWORD'
    APPROVE_USER = 'APPROVE_USER'
    DECLINE_USER = 'DECLINE_USER'
    MUTE_HOURS = 'MUTE_HOURS_'
    MUTE_HOURS_1 = 'MUTE_HOURS_1'
    MUTE_HOURS_12 = 'MUTE_HOURS_12'
    MUTE_HOURS_24 = 'MUTE_HOURS_24'
    MUTE_HOURS_48 = 'MUTE_HOURS_48'
    UNMUTE = 'UNMUTE'
    MUTE_SELECTION = 'MUTE_SELECTION'
    MENU_ACP = 'MENU_ACP'
    MENU_ACP_APPROVE_USER = 'MENU_ACP_APPROVE_USER'
    MENU_ACP_DECLINE_USER = 'MENU_ACP_DECLINE_USER'
    MENU_ACP_ACTIONS = 'MENU_ACP_ACTIONS'
    MENU_ACP_ACTION_TRIGGER_ADMIN = 'MENU_ACP_ACTION_TRIGGER_ADMIN'
    MENU_ACP_ACTION_DELETE_USER = 'MENU_ACP_ACTION_DELETE_USER'


class DATABASES:
    USERS = 'users'
    BOTSTATE = 'botstate'


class USERDB:
    USERNAME = 'username'
    FIRST_NAME = 'first_name'
    LAST_NAME = 'last_name'
    IS_APPROVED = 'is_approved'
    APPROVED_BY = 'approved_by'
    IS_ADMIN = 'is_admin'
    APPROVAL_REQUEST_HAS_BEEN_SENT = 'approval_request_has_been_sent'
    TIMESTAMP_SNOOZE_UNTIL = 'timestamp_snooze_until'
    TIMESTAMP_REGISTERED = 'timestamp_registered'  # Timestamp when user entered correct password
    TIMESTAMP_LAST_SNOOZE = 'timestamp_last_snooze'  # Timestamp when user triggered a snooze last time
    # TIMESTAMP_LAST_PASSWORD_TRY = 'timestamp_last_password_try'
    TIMESTAMP_LAST_APPROVAL_REQUEST = 'timestamp_last_approval_request'
    TIMESTAMP_APPROVED = 'timestamp_approved'


class BOTDB:
    TIMESTAMP_SNOOZE_UNTIL = 'timestamp_snooze_until'
    MUTED_BY_USER_ID = 'muted_by'


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
        if DATABASES.BOTSTATE not in self.couchdb:
            self.couchdb.create(DATABASES.BOTSTATE)
            # Store everything in one doc
            self.couchdb[DATABASES.BOTSTATE][DATABASES.BOTSTATE] = {}

        self.updater = Updater(self.cfg[Config.BOT_TOKEN], request_kwargs={"read_timeout": 30})
        dispatcher = self.updater.dispatcher
        # Main conversation handler - handles nearly all bot menus.
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.botDisplayMenuMain)],
            states={
                CallbackVars.MENU_ASK_FOR_PASSWORD: [
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MENU_MAIN + '$'),
                    MessageHandler(Filters.text, self.botCheckPassword),
                ],
                CallbackVars.MENU_MAIN: [
                    # Main menu
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MENU_MAIN + '$'),
                    CallbackQueryHandler(self.botUnsnooze, pattern='^' + CallbackVars.UNMUTE + '$'),
                    CallbackQueryHandler(self.botSnooze, pattern='^' + CallbackVars.MUTE_HOURS + '\\d+$'),
                    CallbackQueryHandler(self.botAcpDisplayUserList, pattern='^' + CallbackVars.MENU_ACP + '$'),
                ],
                CallbackVars.MENU_ACP: [
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MENU_MAIN + '$'),
                    CallbackQueryHandler(self.botDisplayACPActions, pattern='^' + CallbackVars.MENU_ACP_ACTIONS + '.+$'),
                ],
                CallbackVars.MENU_ACP_ACTIONS: [
                    CallbackQueryHandler(self.botAcpDisplayUserList, pattern='^' + CallbackVars.MENU_ACP + '$'),
                    CallbackQueryHandler(self.botAcpApprovalAllow, pattern='^' + CallbackVars.MENU_ACP_APPROVE_USER + '.+$'),
                    CallbackQueryHandler(self.botAcpUserTriggerAdmin, pattern='^' + CallbackVars.MENU_ACP_ACTION_TRIGGER_ADMIN + '.+$'),
                    CallbackQueryHandler(self.botAcpUserDelete, pattern='^' + CallbackVars.MENU_ACP_ACTION_DELETE_USER + '.+$'),
                ]
            },
            fallbacks=[CommandHandler('start', self.botDisplayMenuMain)],
            name="MainConversationHandler",
        )
        dispatcher.add_handler(conv_handler)
        conv_handler2 = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.botApprovalAllow, pattern='^' + CallbackVars.APPROVE_USER + '.+$'),
                          CallbackQueryHandler(self.botApprovalDeny, pattern='^' + CallbackVars.DECLINE_USER + '.+$')],
            states={
                CallbackVars.APPROVE_USER: [
                    # This is just dummy code. The converstation really starts- and ends right away in the entry_points already!
                    CommandHandler('cancel', self.botDisplayMenuMain),
                ],
            },
            fallbacks=[CommandHandler('start', self.botDisplayMenuMain)],
            name="UserApprovalHandler",
            # allow_reentry=True,
        )
        dispatcher.add_handler(conv_handler2)
        dispatcher.add_error_handler(self.botErrorCallback)

    def botErrorCallback(self, update: Update, context: CallbackContext) -> None:
        try:
            raise context.error
        except BotException as botError:
            menuText = botError.getErrorMsg()
            try:
                update.effective_chat.send_message(menuText, parse_mode="HTML", reply_markup=botError.getReplyMarkup())
            except:
                traceback.print_exc()
                logging.warning('Exception during exception handling -> Raising initial Exception')
                raise botError
        return None

    def isNewUser(self, userID: int) -> bool:
        if str(userID) in self.couchdb[DATABASES.USERS]:
            return False
        else:
            return True

    def userIsApproved(self, userIDStr: str) -> bool:
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None or USERDB.IS_APPROVED not in userDoc:
            return False
        else:
            return userDoc[USERDB.IS_APPROVED]

    def userIsAdmin(self, userIDStr: str) -> bool:
        userDoc = self.getUserDoc(userIDStr)
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
        if not self.userIsApproved(update.effective_user.id):
            menuText = 'Warte auf Freischaltung durch einen Admin.'
            menuText += '\nDu wirst benachrichtigt, sobald dein Account freigeschaltet wurde.'
            userDoc[USERDB.TIMESTAMP_LAST_APPROVAL_REQUEST] = datetime.now().timestamp()
            self.couchdb[DATABASES.USERS].save(userDoc)
            self.botEditOrSendNewMessage(update, context, menuText)
            return CallbackVars.MENU_MAIN
        else:
            menuText = 'Hallo ' + update.effective_user.first_name + ','
            mainMenuKeyboard = []
            if self.getCurrentGlobalSnoozeTimestamp() > datetime.now().timestamp():
                menuText += '\n' + SYMBOLS.WARNING + 'Benachrichtigungen deaktiviert bis: ' + formatTimestampToGermanDate(
                    self.getCurrentGlobalSnoozeTimestamp()) + ' (noch ' + getFormattedTimeDelta(self.getCurrentGlobalSnoozeTimestamp()) + ')'
                menuText += '\nVon: ' + self.getMeaningfulUserTitle(self.getCurrentGlobalSnoozeUserID())
                mainMenuKeyboard.append([InlineKeyboardButton('Benachrichtigungen für alle aktivieren', callback_data=CallbackVars.UNMUTE)])
            else:
                # Cleanup DB
                if USERDB.TIMESTAMP_SNOOZE_UNTIL in userDoc:
                    del userDoc[USERDB.TIMESTAMP_SNOOZE_UNTIL]
                    self.couchdb[DATABASES.USERS].save(userDoc)
                menuText += '\nhier kannst du Aktivitäten-Benachrichtigungen abschalten.'
                mainMenuKeyboard.append([InlineKeyboardButton('1 Stunde', callback_data=CallbackVars.MUTE_HOURS_1),
                                         InlineKeyboardButton('12 Stunden', callback_data=CallbackVars.MUTE_HOURS_12)])
                mainMenuKeyboard.append([InlineKeyboardButton('24 Stunden', callback_data=CallbackVars.MUTE_HOURS_24),
                                         InlineKeyboardButton('48 Stunden', callback_data=CallbackVars.MUTE_HOURS_48)])
            if self.userIsAdmin(update.effective_user.id):
                menuText += '\n' + SYMBOLS.CONFIRM + '<b>Du bist Admin!</b>'
                menuText += '\nMissbrauche deine Macht nicht!'
                mainMenuKeyboard.append([InlineKeyboardButton('ACP', callback_data=CallbackVars.MENU_ACP)])
            self.botEditOrSendNewMessage(update, context, menuText,
                                         reply_markup=InlineKeyboardMarkup(mainMenuKeyboard))
        return CallbackVars.MENU_MAIN

    def botAcpDisplayUserList(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        if not self.userIsAdmin(str(update.effective_user.id)):
            self.errorAdminRightsRequired()
        users = self.getAllUsersExceptOne(str(update.effective_user.id))
        acpKeyboard = []
        for userIDStr in users:
            userPrefix = self.getUserRightsPrefix(userIDStr)
            acpKeyboard.append([InlineKeyboardButton(userPrefix + self.getMeaningfulUserTitle(userIDStr), callback_data=CallbackVars.MENU_ACP_ACTIONS + userIDStr)])
        acpKeyboard.append([InlineKeyboardButton(SYMBOLS.BACK + 'Zurück ', callback_data=CallbackVars.MENU_MAIN)])
        menuText = "Benutzer werden nicht über Änderungen informiert!!"
        menuText += "\n<b>Obacht</b>: Alle Aktionen passieren sofort und ohne Notwendigkeit einer Bestätigung!"
        menuText += "\n" + SYMBOLS.STAR + " = Admin"
        menuText += "\n" + SYMBOLS.WARNING + " = Unbestätigter User"
        self.botEditOrSendNewMessage(update, context, menuText, reply_markup=InlineKeyboardMarkup(acpKeyboard))
        return CallbackVars.MENU_ACP

    def botDisplayACPActions(self, update: Update, context: CallbackContext):
        query = update.callback_query
        if query is not None:
            query.answer()
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_ACTIONS, "")
        return self.acpDisplayUserActions(update, context, userIDStr)

    def acpDisplayUserActions(self, update: Update, context: CallbackContext, userIDStr):
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None:
            return CallbackVars.MENU_ACP
        userOptions = []
        menuText = "Verwalte Benutzer: " + self.getUserRightsPrefix(userIDStr) + self.getMeaningfulUserTitle(userIDStr)
        if not self.userIsApproved(userIDStr):
            userOptions.append([InlineKeyboardButton(SYMBOLS.CONFIRM + 'Benutzer bestätigen', callback_data=CallbackVars.MENU_ACP_APPROVE_USER + userIDStr)])
        else:
            if self.userIsAdmin(userIDStr):
                userOptions.append([InlineKeyboardButton(SYMBOLS.DENY + 'Admin entfernen', callback_data=CallbackVars.MENU_ACP_ACTION_TRIGGER_ADMIN + userIDStr)])
            else:
                userOptions.append([InlineKeyboardButton(SYMBOLS.PLUS + 'Admin', callback_data=CallbackVars.MENU_ACP_ACTION_TRIGGER_ADMIN + userIDStr)])
            # TODO: Add functionality or remove this
            userOptions.append([InlineKeyboardButton('Snooze Spamschutz entfernen*', callback_data=CallbackVars.MENU_MAIN)])
            menuText += "\nBenutzer bestätigt von: " + self.getMeaningfulUserTitle(userDoc[USERDB.APPROVED_BY])
            menuText += "\n* = Button ohne Funktionalität"
        userOptions.append([InlineKeyboardButton(SYMBOLS.DENY + 'Löschen', callback_data=CallbackVars.MENU_ACP_ACTION_DELETE_USER + userIDStr)])
        userOptions.append([InlineKeyboardButton(SYMBOLS.BACK + 'Zurück', callback_data=CallbackVars.MENU_ACP)])
        self.botEditOrSendNewMessage(update, context, menuText,
                                     reply_markup=InlineKeyboardMarkup(userOptions))
        return CallbackVars.MENU_ACP_ACTIONS

    def botSnooze(self, update: Update, context: CallbackContext):
        query = update.callback_query
        # query.answer()
        if self.getCurrentGlobalSnoozeTimestamp() < datetime.now().timestamp():
            snoozeHours = int(query.data.replace(CallbackVars.MUTE_HOURS, ""))
            snoozeUntil = datetime.now().timestamp() + snoozeHours * 60 * 60
            # Save user state
            userDoc = self.getUserDoc(update.effective_user.id)
            userDoc[USERDB.TIMESTAMP_SNOOZE_UNTIL] = snoozeUntil
            userDoc[USERDB.TIMESTAMP_LAST_SNOOZE] = datetime.now().timestamp()
            self.couchdb[DATABASES.USERS].save(userDoc)
            # Save global state
            botDoc = self.couchdb[DATABASES.BOTSTATE][DATABASES.BOTSTATE]
            botDoc[BOTDB.TIMESTAMP_SNOOZE_UNTIL] = snoozeUntil
            botDoc[BOTDB.MUTED_BY_USER_ID] = update.effective_user.id
            self.couchdb[DATABASES.BOTSTATE].save(botDoc)
            text = SYMBOLS.WARNING + self.getMeaningfulUserTitle(self.getCurrentGlobalSnoozeUserID()) + " hat Benachrichtigungen deaktiviert bis: " + formatTimestampToGermanDate(
                self.getCurrentGlobalSnoozeTimestamp()) + ' (noch ' + getFormattedTimeDelta(self.getCurrentGlobalSnoozeTimestamp()) + ')!'
            text += '\nMit /start siehst du den aktuellen Stand.'
            self.sendMessageToMultipleUsers(self.getApprovedUsersExceptOne(str(update.effective_user.id)), text)
        else:
            logging.info("User attempted snooze but snooze is already active: " + str(update.effective_user.id))
        return self.botDisplayMenuMain(update, context)

    def botUnsnooze(self, update: Update, context: CallbackContext):
        # Save global state
        botDoc = self.getBotDoc()
        if BOTDB.TIMESTAMP_SNOOZE_UNTIL in botDoc:
            del botDoc[BOTDB.TIMESTAMP_SNOOZE_UNTIL]
        if BOTDB.MUTED_BY_USER_ID in botDoc:
            del botDoc[BOTDB.MUTED_BY_USER_ID]
        self.couchdb[DATABASES.BOTSTATE].save(botDoc)
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
            self.approveUser(userIDStr, str(update.effective_user.id))
            self.notifyUserApproved(int(userIDStr))
        return ConversationHandler.END

    def botAcpApprovalAllow(self, update: Update, context: CallbackContext):
        query = update.callback_query
        # Very important: Even non-admins could in theory trigger such actions if they're still in the admin menu -> Ensure that they can't do so!
        if not self.userIsAdmin(str(update.effective_user.id)):
            query.answer()
            self.errorAdminRightsRequired()
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_APPROVE_USER, "")
        self.approveUser(userIDStr, update.effective_user.id)
        return self.acpDisplayUserActions(update, context, userIDStr)

    def getUserRightsPrefix(self, userIDStr) -> str:
        """ Returns prefox based on current users rights/state e.g. admin or user waiting for approval. """
        if not self.userIsApproved(userIDStr):
            return SYMBOLS.WARNING
        elif self.userIsAdmin(userIDStr):
            return SYMBOLS.STAR
        else:
            return ''

    def approveUser(self, userIDStr, approvedByUserIDStr) -> None:
        """
        :param userIDStr: ID of the user to approve
        :param approvedByUserIDStr: ID of the user that wants to approve the other user.
        :return: None
        """
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None:
            logging.warning("User approval failed: userID doesn't exist in DB")
            return
        userDoc[USERDB.IS_APPROVED] = True
        userDoc[USERDB.APPROVED_BY] = approvedByUserIDStr
        userDoc[USERDB.TIMESTAMP_APPROVED] = datetime.now().timestamp()
        self.couchdb[DATABASES.USERS].save(userDoc)

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
                # Update DB
                self.couchdb[DATABASES.USERS][str(update.effective_user.id)] = userData
                # Small "workaround" as first user is basically approved by itself!
                self.approveUser(str(update.effective_user.id), str(update.effective_user.id))
                text += "\n<b>Du bist der erste User -> Admin!</b>"
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
             InlineKeyboardButton(SYMBOLS.DENY + 'Ablehnen/Löschen', callback_data=CallbackVars.DECLINE_USER + str(userID))]
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
            text = SYMBOLS.DENY + "<b>Fehler Alarmanlage!Keine neuen Daten verfügbar!\nLetzte Sensordaten vom: " + formatDatetimeToGermanDate(
                self.lastSensorUpdateDatetime) + "</b>\n" + alarmMessages
            self.sendMessageToAllApprovedUsers(text)
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
        if len(alarmSensorsNames) > 0 and not self.isGloballySnoozed():
            logging.warning("Sending out alarms...")
            text = "<b>Alarm! " + channelInfo['name'] + "</b>"
            text += '\n' + formatDatetimeToGermanDate(alarmDatetime) + ' | Sensoren: ' + ', '.join(alarmSensorsNames)
            # Sending those alarms can take some time thus let's update this timestamp here already
            self.lastAlarmSentTimestamp = datetime.now().timestamp()
            self.sendMessageToAllApprovedUsers(text)
        self.lastEntryID = currentLastEntryID
        self.lastEntryIDChangeTimestamp = datetime.now().timestamp()

    def getCurrentGlobalSnoozeTimestamp(self) -> float:
        return self.getBotDoc().get(BOTDB.TIMESTAMP_SNOOZE_UNTIL, 0)

    def getCurrentGlobalSnoozeUserID(self):
        """ Returns ID of user who activated last snooze. """
        return self.getBotDoc().get(BOTDB.MUTED_BY_USER_ID, "WTF")

    def isGloballySnoozed(self):
        return self.getCurrentGlobalSnoozeTimestamp() > datetime.now().timestamp()

    def sendMessageToAllApprovedUsers(self, text: str):
        approvedUsers = self.getApprovedUsers()
        self.sendMessageToMultipleUsers(approvedUsers, text)

    def sendMessageToMultipleUsers(self, users: dict, text: str):
        logging.info("Sending messages to " + str(len(users)) + " users...")
        for userID in users:
            userDoc = users[userID]
            try:
                self.updater.bot.send_message(chat_id=userID, text=text, parse_mode='HTML')
            except BadRequest:
                # Maybe user has blocked bot
                pass

    def getMeaningfulUserTitle(self, userID) -> str:
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            return "Gelöschter Benutzer"
        if USERDB.USERNAME in userDoc:
            fullname = "@" + userDoc[USERDB.USERNAME]
        else:
            fullname = str(userID)
        fullname += " (" + userDoc[USERDB.FIRST_NAME]
        if USERDB.LAST_NAME in userDoc:
            fullname += " " + userDoc[USERDB.LAST_NAME]
        fullname += ")"
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

    def getApprovedUsersExceptOne(self, ignoreUserID: str) -> dict:
        """ Returns approved users AND admins """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            if userID == ignoreUserID:
                continue
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False):
                users[userID] = userDoc
        return users

    def getAllUsersExceptOne(self, ignoreUserID: str) -> dict:
        """ Returns approved users AND admins """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            if userID == ignoreUserID:
                continue
            userDoc = userDB[userID]
            users[userID] = userDoc
        return users

    def botAcpUserTriggerAdmin(self, update: Update, context: CallbackContext):
        query = update.callback_query
        # Very important: Even non-admins could in theory trigger such actions if they're still in the admin menu -> Ensure that they can't do so!
        if not self.userIsAdmin(str(update.effective_user.id)):
            query.answer()
            self.errorAdminRightsRequired()
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_ACTION_TRIGGER_ADMIN, "")
        self.userTriggerAdmin(userIDStr)
        return self.acpDisplayUserActions(update, context, userIDStr)

    def userTriggerAdmin(self, userIDStr):
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None:
            return
        elif userDoc.get(USERDB.IS_ADMIN, False):
            userDoc[USERDB.IS_ADMIN] = False
            self.couchdb[DATABASES.USERS].save(userDoc)
        else:
            userDoc[USERDB.IS_ADMIN] = True
            self.couchdb[DATABASES.USERS].save(userDoc)

    def botAcpUserDelete(self, update: Update, context: CallbackContext):
        query = update.callback_query
        # Very important: Even non-admins could in theory trigger such actions if they're still in the admin menu -> Ensure that they can't do so!
        if not self.userIsAdmin(str(update.effective_user.id)):
            query.answer()
            self.errorAdminRightsRequired()
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_ACTION_DELETE_USER, "")
        self.userDelete(userIDStr)
        return self.botAcpDisplayUserList(update, context)

    def userDelete(self, userIDStr):
        """ Deletes user from DB. """
        if userIDStr in self.couchdb[DATABASES.USERS]:
            del self.couchdb[DATABASES.USERS][userIDStr]

    # def getApprovedUnmutedUsers(self) -> dict:
    #     """ Returns approved users AND admins """
    #     users = {}
    #     userDB = self.couchdb[DATABASES.USERS]
    #     for userID in userDB:
    #         userDoc = userDB[userID]
    #         if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False) and userDoc.get(USERDB.TIMESTAMP_SNOOZE_UNTIL, datetime.now().timestamp()) <= datetime.now().timestamp():
    #             users[userID] = userDoc
    #     return users

    def getUserDoc(self, userID):
        return self.couchdb[DATABASES.USERS].get(str(userID))

    def getBotDoc(self):
        return self.couchdb[DATABASES.BOTSTATE][DATABASES.BOTSTATE]

    def handleBatchProcess(self):
        try:
            self.updateNotifications()
        except:
            traceback.print_exc()
            logging.warning("Batchprocess failed")

    def errorAdminRightsRequired(self):
        raise BotException(SYMBOLS.WARNING + "Nur Admins dürfen diese Aktion ausführen!")


if __name__ == '__main__':
    bot = ABBot()
    bot.updater.start_polling()
    schedule.every(5).seconds.do(bot.handleBatchProcess)
    while True:
        schedule.run_pending()
        time.sleep(1)
