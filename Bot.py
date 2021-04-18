import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Union

import couchdb
import schedule
from telegram import Update, ReplyMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Unauthorized
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
    TIMESTAMP_LAST_BLOCKED_BOT_ERROR = 'timestamp_last_blocked_bot_error'


class BOTDB:
    TIMESTAMP_SNOOZE_UNTIL = 'timestamp_snooze_until'
    MUTED_BY_USER_ID = 'muted_by'


class ABBot:

    def __init__(self):
        self.cfg = loadConfig()
        if self.cfg is None or self.cfg.get(Config.DB_URL) is None:
            raise Exception('Broken config')
        # Init CouchDB
        self.couchdb = couchdb.Server(self.cfg[Config.DB_URL])
        # Create required DBs
        if DATABASES.USERS not in self.couchdb:
            self.couchdb.create(DATABASES.USERS)
        if DATABASES.BOTSTATE not in self.couchdb:
            self.couchdb.create(DATABASES.BOTSTATE)
            # Store everything in one doc
            self.couchdb[DATABASES.BOTSTATE][DATABASES.BOTSTATE] = {}
        # Init other fields
        self.lastEntryID = -1
        self.lastAlarmSentTimestamp = -1
        self.lastEntryIDChangeTimestamp = -1
        self.lastTimeNoNewSensorDataAvailableWarningSentTimestamp = -1
        self.lastSensorUpdateDatetime = datetime.now()
        self.lastFieldIDToSensorsMapping = {}
        # Now comes all the bot related stuff
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
                self.sendMessage(chat_id=update.effective_user.id, text=menuText, reply_markup=botError.getReplyMarkup())
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
                menuText += '\nDie Person, die den Alarm deaktiviert hat sollte ihn selbstständig wieder aktivieren also Finger weg es sei denn dies wurde vergessen!'
                mainMenuKeyboard.append([InlineKeyboardButton('Benachrichtigungen für alle aktivieren', callback_data=CallbackVars.UNMUTE)])
            else:
                menuText += '\nhier kannst du Aktivitäten-Benachrichtigungen abschalten.'
                menuText += '\nAlle Bot User werden benachrichtigt wenn du einen den Buttons drückst also lass\' bitte deinen Spieltrieb beiseite!'
                mainMenuKeyboard.append([InlineKeyboardButton('1 Stunde', callback_data=CallbackVars.MUTE_HOURS + '1'),
                                         InlineKeyboardButton('12 Stunden', callback_data=CallbackVars.MUTE_HOURS + '12')])
                mainMenuKeyboard.append([InlineKeyboardButton('24 Stunden', callback_data=CallbackVars.MUTE_HOURS + '24'),
                                         InlineKeyboardButton('48 Stunden', callback_data=CallbackVars.MUTE_HOURS + '48')])
            if self.userIsAdmin(update.effective_user.id):
                menuText += '\n' + SYMBOLS.CONFIRM + '<b>Du bist Admin!</b>'
                menuText += '\nMissbrauche deine Macht nicht!'
                if len(self.lastFieldIDToSensorsMapping) > 0:
                    menuText += "\nLetzte Sensordaten"
                    for sensorID, sensor in self.lastFieldIDToSensorsMapping.items():
                        menuText += "\n" + sensor.getName() + ": " + str(sensor.getValue())
                mainMenuKeyboard.append([InlineKeyboardButton('ACP', callback_data=CallbackVars.MENU_ACP)])
            self.botEditOrSendNewMessage(update, context, menuText,
                                         reply_markup=InlineKeyboardMarkup(mainMenuKeyboard))
        return CallbackVars.MENU_MAIN

    def botAcpDisplayUserList(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        self.adminOrException(update.effective_user.id)
        users = self.getAllUsersExceptOne(str(update.effective_user.id))
        acpKeyboard = []
        if len(users) == 0:
            # Edge-case
            menuText = "<b>Es gibt außer dir noch keine Benutzer!</b>"
        else:
            for userIDStr in users:
                userPrefix = self.getUserRightsPrefix(userIDStr)
                acpKeyboard.append([InlineKeyboardButton(userPrefix + self.getMeaningfulUserTitle(userIDStr), callback_data=CallbackVars.MENU_ACP_ACTIONS + userIDStr)])
            menuText = "<b>Benutzerliste:</b>"
            menuText += "\nBenutzer werden nicht über Änderungen informiert!"
            menuText += "\n<b>Obacht</b>: Alle Aktionen passieren sofort und ohne Notwendigkeit einer Bestätigung!"
            menuText += "\n" + SYMBOLS.STAR + " = Admin"
            menuText += "\n" + SYMBOLS.WARNING + " = Unbestätigter User"
        acpKeyboard.append([InlineKeyboardButton(SYMBOLS.BACK + 'Zurück ', callback_data=CallbackVars.MENU_MAIN)])
        self.botEditOrSendNewMessage(update, context, menuText, reply_markup=InlineKeyboardMarkup(acpKeyboard))
        return CallbackVars.MENU_ACP

    def botDisplayACPActions(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        self.adminOrException(update.effective_user.id)
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
            # userOptions.append([InlineKeyboardButton('Snooze Spamschutz entfernen*', callback_data=CallbackVars.MENU_MAIN)])
            menuText += "\nRegistriert am: " + formatTimestampToGermanDate(userDoc[USERDB.TIMESTAMP_REGISTERED])
            menuText += "\nBestätigt von: " + self.getMeaningfulUserTitle(userDoc[USERDB.APPROVED_BY])
            menuText += "\nBestätigt am: " + formatTimestampToGermanDate(userDoc[USERDB.TIMESTAMP_APPROVED])
            menuText += "\n*Mit Sternchen gekennzeichnete Buttons = Buttons ohne Funktionalität!"
            menuText += "\nLöschen = Benutzer muss sich erneut mit Passwort anmelden und bestätigt werden und kann den Bot ansonsten nicht mehr verwenden."
        userOptions.append([InlineKeyboardButton(SYMBOLS.DENY + 'Löschen', callback_data=CallbackVars.MENU_ACP_ACTION_DELETE_USER + userIDStr)])
        userOptions.append([InlineKeyboardButton(SYMBOLS.BACK + 'Zurück', callback_data=CallbackVars.MENU_ACP)])
        self.botEditOrSendNewMessage(update, context, menuText,
                                     reply_markup=InlineKeyboardMarkup(userOptions))
        return CallbackVars.MENU_ACP_ACTIONS

    def botSnooze(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        if self.getCurrentGlobalSnoozeTimestamp() < datetime.now().timestamp():
            snoozeHours = int(query.data.replace(CallbackVars.MUTE_HOURS, ""))
            snoozeUntil = datetime.now().timestamp() + snoozeHours * 60 * 60
            # Save user state first. This also ensures that an exception will happen if that user e.g. has been removed from DB recently and presses a button afterwards!
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
        """ Activates notifications for all users. """
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
            self.notifyUserApproved(userIDStr)
        return ConversationHandler.END

    def botAcpApprovalAllow(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        # Very important: Even non-admins could in theory trigger such actions if they're still in the admin menu -> Ensure that they can't do so!
        self.adminOrException(update.effective_user.id)
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_APPROVE_USER, "")
        self.approveUser(userIDStr, update.effective_user.id)
        return self.acpDisplayUserActions(update, context, userIDStr)

    def getUserRightsPrefix(self, userIDStr) -> str:
        """ Returns prefix based on current users rights/state e.g. admin or user waiting for approval. """
        if not self.userIsApproved(userIDStr):
            return SYMBOLS.WARNING
        elif self.userIsAdmin(userIDStr):
            return SYMBOLS.STAR
        else:
            return ''

    def approveUser(self, userID, approvedByUserID: Union[int, str]) -> None:
        """
        :param userID: ID of the user to approve
        :param approvedByUserID: ID of the user that wants to approve the other user.
        :return: None
        """
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            logging.warning("User approval failed: userID doesn't exist in DB")
            return
        userDoc[USERDB.IS_APPROVED] = True
        userDoc[USERDB.APPROVED_BY] = approvedByUserID
        userDoc[USERDB.TIMESTAMP_APPROVED] = datetime.now().timestamp()
        self.couchdb[DATABASES.USERS].save(userDoc)

    def notifyUserApproved(self, userID: Union[int, str]) -> None:
        text = SYMBOLS.CONFIRM + "Du wurdest freigeschaltet!"
        text += "\nMit /start gelangst du in's Hauptmenü."
        self.sendMessage(userID, text)

    def notifyUserDeny(self, userID: Union[int, str]) -> None:
        self.sendMessage(userID, SYMBOLS.DENY + "Du wurdest abgelehnt!")

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
            self.notifyUserDeny(userIDStr)
        return ConversationHandler.END

    def userExistsInDB(self, userID: Union[int, str]) -> bool:
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
            self.sendMessage(update.effective_message.chat_id, text)
            return self.botDisplayMenuMain(update, context)
        else:
            self.sendMessage(chat_id=update.effective_message.chat_id, text=SYMBOLS.DENY + "Falsches Passwort!")
            return CallbackVars.MENU_ASK_FOR_PASSWORD

    def botEditOrSendNewMessage(self, update: Update, context: CallbackContext, text: str,
                                reply_markup: ReplyMarkup = None):
        query = update.callback_query
        if query is not None:
            query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            context.bot.send_message(chat_id=update.effective_message.chat_id, reply_markup=reply_markup, text=text,
                                     parse_mode='HTML')

    def sendUserApprovalRequests(self) -> None:
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

    def sendUserApprovalRequestToAllAdmins(self, userID: Union[int, str]) -> None:
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
            self.sendMessage(adminUserID, menuText, reply_markup=reply_markup)
            index += 1
        userDoc[USERDB.APPROVAL_REQUEST_HAS_BEEN_SENT] = True
        userDB.save(userDoc)

    def sendAlarmNotifications(self):
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
        elif currentLastEntryID == self.lastEntryID and datetime.now().timestamp() - self.lastEntryIDChangeTimestamp >= 10 * 60:
            # Check if our alarm system maybe hasn't been responding for a long amount of time
            lastSensorDataIsFromDate = formatDatetimeToGermanDate(self.lastSensorUpdateDatetime)
            logging.warning("Got no new sensor data for some time! Last data is from: " + lastSensorDataIsFromDate)
            if datetime.now().timestamp() - self.lastTimeNoNewSensorDataAvailableWarningSentTimestamp > 60 * 60 and not self.isGloballySnoozed():
                text = SYMBOLS.DENY + "<b>Fehler Alarmanlage!Keine neuen Daten verfügbar!\nLetzte Sensordaten vom: " + lastSensorDataIsFromDate + "</b>\n" + alarmMessages
                self.sendMessageToAllApprovedUsers(text)
                self.lastTimeNoNewSensorDataAvailableWarningSentTimestamp = datetime.now().timestamp()
            return
        elif currentLastEntryID == self.lastEntryID:
            logging.info("last_entry_id hasn't changed - it still is: " + str(currentLastEntryID) + " --> No new data available --> Last data is from: " + formatDatetimeToGermanDate(self.lastSensorUpdateDatetime))
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
            fieldIDToSensorMapping[int(fieldIDStr)] = Sensor(name=channelInfo[fieldKey], triggerValue=sensorUserConfig['trigger'], triggerOperator=sensorUserConfig['operator'], alarmOnlyOnceUntilUntriggered=sensorUserConfig['alarmOnlyOnceUntilUntriggered'])
        alarmDatetime = None
        alarmSensorsNames = []
        alarmSensorsDebugTextStrings = []
        for feed in sensorResults:
            # Check all fields for which we got alarm state mapping
            entryID = feed['entry_id']
            if entryID <= self.lastEntryID and checkOnlyHigherEntryIDs:
                # Ignore entries we've checked before
                continue
            for fieldID, sensor in fieldIDToSensorMapping.items():
                fieldKey = 'field' + str(fieldID)
                currentFieldValue = float(feed[fieldKey])
                sensor.setValue(currentFieldValue)
                thisDatetime = datetime.strptime(feed['created_at'], '%Y-%m-%dT%H:%M:%S%z')
                self.lastSensorUpdateDatetime = thisDatetime
                # Check if alarm state is given
                if sensor.isTriggered():
                    if sensor.isAlarmOnlyOnceUntilUntriggered() and fieldID in self.lastFieldIDToSensorsMapping and self.lastFieldIDToSensorsMapping[fieldID].isTriggered():
                        # print("Ignore " + sensor.getName() + " because alarm is only allowed once until untriggered")
                        logging.info("Ignore " + sensor.getName() + " because alarm is only allowed once until untriggered")
                        continue
                    alarmDatetime = thisDatetime
                    if sensor.getName() not in alarmSensorsNames:
                        alarmSensorsNames.append(sensor.getName())
                        alarmSensorsDebugTextStrings.append(sensor.getName() + "(" + fieldKey + ")")
        if len(alarmSensorsNames) > 0:
            print("Alarms triggered: " + formatDatetimeToGermanDate(alarmDatetime) + " | " + ', '.join(alarmSensorsNames))
            if self.isGloballySnoozed():
                print("Not sending alarms because: Snoozed")
            elif datetime.now().timestamp() < (self.lastAlarmSentTimestamp + 1 * 60):
                # Only allow alarms every X minutes otherwise we'd send new messages every time this code gets executed!
                print("Not sending alarms because: Flood protection")
            else:
                logging.warning("Sending out alarms...")
                text = "<b>Alarm! " + channelInfo['name'] + "</b>"
                text += '\n' + formatDatetimeToGermanDate(alarmDatetime) + ' | Sensoren: ' + ', '.join(alarmSensorsNames)
                # Sending those alarms can take some time thus let's update this timestamp here already
                self.lastAlarmSentTimestamp = datetime.now().timestamp()
                self.sendMessageToAllApprovedUsers(text)

        self.lastFieldIDToSensorsMapping = fieldIDToSensorMapping.copy()
        self.lastEntryID = currentLastEntryID
        self.lastEntryIDChangeTimestamp = datetime.now().timestamp()

    def getCurrentGlobalSnoozeTimestamp(self) -> float:
        return self.getBotDoc().get(BOTDB.TIMESTAMP_SNOOZE_UNTIL, 0)

    def getCurrentGlobalSnoozeUserID(self) -> str:
        """ Returns ID of user who activated last snooze. """
        return self.getBotDoc().get(BOTDB.MUTED_BY_USER_ID, "WTF")

    def isGloballySnoozed(self) -> bool:
        return self.getCurrentGlobalSnoozeTimestamp() > datetime.now().timestamp()

    def sendMessageToAllApprovedUsers(self, text: str):
        approvedUsers = self.getApprovedUsers()
        self.sendMessageToMultipleUsers(approvedUsers, text)

    def sendMessageToMultipleUsers(self, users: dict, text: str):
        logging.info("Sending messages to " + str(len(users)) + " users...")
        for userID in users:
            self.sendMessage(userID, text)

    def sendMessage(self, chat_id: Union[int, str], text: str, reply_markup=None):
        try:
            self.updater.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
        except BadRequest:
            pass
        except Unauthorized:
            # E.g. user has blocked bot -> Save that so we can remove such users on DB cleanup
            userDoc = self.getUserDoc(chat_id)
            if userDoc is not None:
                userDoc[USERDB.TIMESTAMP_LAST_BLOCKED_BOT_ERROR] = datetime.now().timestamp()
                self.couchdb[DATABASES.USERS].save(userDoc)
            pass

    def getMeaningfulUserTitle(self, userID) -> str:
        """ This will usually return something like "@ExampleUsername (FirstName LastName)". """
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            # This should never happen
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
        """ Returns approved users and admins. """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False):
                users[userID] = userDoc
        return users

    def getApprovedUsersExceptOne(self, ignoreUserID: str) -> dict:
        """ Returns approved users and admins. """
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
        """ Returns ALL users and admins. """
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
        query.answer()
        self.adminOrException(update.effective_user.id)
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_ACTION_TRIGGER_ADMIN, "")
        self.userTriggerAdmin(userIDStr)
        return self.acpDisplayUserActions(update, context, userIDStr)

    def userTriggerAdmin(self, userIDStr: str):
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
        query.answer()
        self.adminOrException(update.effective_user.id)
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_ACTION_DELETE_USER, "")
        self.userDelete(userIDStr)
        return self.botAcpDisplayUserList(update, context)

    def userDelete(self, userIDStr) -> bool:
        """ Deletes user from DB. """
        if userIDStr in self.couchdb[DATABASES.USERS]:
            del self.couchdb[DATABASES.USERS][userIDStr]
            return True
        else:
            return False

    # def getApprovedUnmutedUsers(self) -> dict:
    #     """ Returns approved users AND admins """
    #     users = {}
    #     userDB = self.couchdb[DATABASES.USERS]
    #     for userID in userDB:
    #         userDoc = userDB[userID]
    #         if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False) and userDoc.get(USERDB.TIMESTAMP_SNOOZE_UNTIL, datetime.now().timestamp()) <= datetime.now().timestamp():
    #             users[userID] = userDoc
    #     return users

    def getUserDoc(self, userID: Union[int, str]):
        return self.couchdb[DATABASES.USERS].get(str(userID))

    def getBotDoc(self):
        return self.couchdb[DATABASES.BOTSTATE][DATABASES.BOTSTATE]

    def handleBatchProcess(self) -> None:
        try:
            self.sendAlarmNotifications()
        except:
            traceback.print_exc()
            logging.warning("Batchprocess failed")

    def adminOrException(self, userID: Union[int, str]):
        if not self.userIsAdmin(userID):
            self.errorAdminRightsRequired()

    def errorAdminRightsRequired(self) -> None:
        raise BotException(SYMBOLS.WARNING + "Nur Admins dürfen diese Aktion ausführen!")


if __name__ == '__main__':
    bot = ABBot()
    bot.updater.start_polling()
    schedule.every(5).seconds.do(bot.handleBatchProcess)
    while True:
        schedule.run_pending()
        time.sleep(1)
