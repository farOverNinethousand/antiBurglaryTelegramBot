import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Union

import couchdb
import schedule
from telegram import Update, ReplyMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest, Unauthorized
from telegram.ext import Updater, ConversationHandler, CommandHandler, CallbackContext, CallbackQueryHandler, \
    MessageHandler, Filters

from AlarmSystem import AlarmSystem
from Helper import Config, loadConfig, SYMBOLS, getFormattedTimeDelta, formatTimestampToGermanDate, BotException, formatDatetimeToGermanDate

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


class CallbackVars:
    MENU_MAIN = 'MENU_MAIN'
    MENU_ASK_FOR_PASSWORD = 'MENU_ASK_FOR_PASSWORD'
    APPROVE_USER = 'APPROVE_USER'
    DECLINE_USER = 'DECLINE_USER'
    MUTE_HOURS = 'MUTE_HOURS_'
    UNMUTE = 'UNMUTE'
    SEND_BROADCAST = 'SEND_BROADCAST'
    # MUTE_SELECTION = 'MUTE_SELECTION'
    MENU_SETTINGS = 'MENU_SETTINGS'
    MENU_SETTINGS_DISPLAY_OWN_DATA = 'MENU_SETTINGS_DISPLAY_OWN_DATA'
    MENU_SETTINGS_DELETE_ACCOUNT = 'MENU_SETTINGS_DELETE_ACCOUNT'
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
    TIMESTAMP_LAST_BROADCAST_SENT = 'timestamp_last_broadcast_sent'
    # TIMESTAMP_LAST_PASSWORD_TRY = 'timestamp_last_password_try'
    TIMESTAMP_LAST_APPROVAL_REQUEST = 'timestamp_last_approval_request'
    TIMESTAMP_APPROVED = 'timestamp_approved'
    TIMESTAMP_LAST_TIME_REQUESTED_DSGVO_DATA = 'timestamp_last_time_requested_dsgvo_data'
    TIMESTAMP_LAST_BLOCKED_BOT_ERROR = 'timestamp_last_blocked_bot_error'
    MSG_ID_LAST_SNOOZE_NOTIFICATION = 'msg_id_last_snooze_notification'
    MSG_IDS_APPROVAL_REQUESTS = 'msg_ids_approval_requests'


class BOTDB:
    TIMESTAMP_SNOOZE_UNTIL = 'timestamp_snooze_until'
    MUTED_BY_USER_ID = 'muted_by'


BOT_VERSION = "0.8.5"


class ABBot:

    def __init__(self):
        self.cfg = loadConfig()
        if self.cfg is None or self.cfg.get(Config.DB_URL) is None:
            raise Exception('Broken config')
        # Init CouchDB
        self.couchdb = couchdb.Server(self.cfg[Config.DB_URL])
        self.alarmsystem = AlarmSystem(self.cfg)
        self.alarmsystem.setAlarmIntervalNoData(-1)
        # Init that
        self.alarmsystem.updateAlarms()
        # Create required DBs
        if DATABASES.USERS not in self.couchdb:
            self.couchdb.create(DATABASES.USERS)
        if DATABASES.BOTSTATE not in self.couchdb:
            self.couchdb.create(DATABASES.BOTSTATE)
            # Store everything in one doc
            self.couchdb[DATABASES.BOTSTATE][DATABASES.BOTSTATE] = {}
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
                    CallbackQueryHandler(self.botSendUserDefinedBroadcastSTART, pattern='^' + CallbackVars.SEND_BROADCAST + '$'),
                    CallbackQueryHandler(self.botDisplaySettings, pattern='^' + CallbackVars.MENU_SETTINGS + '$'),
                    CallbackQueryHandler(self.botAcpDisplayUserList, pattern='^' + CallbackVars.MENU_ACP + '$'),
                    MessageHandler(filters=Filters.text and (~Filters.command), callback=self.botWTF),
                ],
                CallbackVars.SEND_BROADCAST: [
                    # Go back to main menu if user enters ANY command.
                    MessageHandler(Filters.command, self.botDisplayMenuMain),
                    MessageHandler(Filters.text, self.botSendUserDefinedBroadcast),
                ],
                CallbackVars.MENU_SETTINGS: [
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MENU_MAIN + '$'),
                    CallbackQueryHandler(self.botDisplayOwnUserData, pattern='^' + CallbackVars.MENU_SETTINGS_DISPLAY_OWN_DATA + '$'),
                    CallbackQueryHandler(self.botDeleteOwnAccountSTART, pattern='^' + CallbackVars.MENU_SETTINGS_DELETE_ACCOUNT + '$'),
                ],
                CallbackVars.MENU_SETTINGS_DISPLAY_OWN_DATA: [
                    # Back button
                    CallbackQueryHandler(self.botDisplaySettings, pattern='^' + CallbackVars.MENU_SETTINGS + '$'),
                ],
                CallbackVars.MENU_SETTINGS_DELETE_ACCOUNT: [
                    CommandHandler('cancel', self.botDisplayMenuMain),
                    CommandHandler('start', self.botDisplayMenuMain),
                    MessageHandler(Filters.text, self.botDeleteOwnAccount),
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

    def userIsApproved(self, userID: Union[int, str]) -> bool:
        userDoc = self.getUserDoc(userID)
        if userDoc is None or USERDB.IS_APPROVED not in userDoc:
            return False
        else:
            return userDoc[USERDB.IS_APPROVED]

    def userIsAdmin(self, userID: Union[int, str]) -> bool:
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
        if update.effective_user.last_name is not None:
            userDoc[USERDB.LAST_NAME] = update.effective_user.last_name
        elif USERDB.LAST_NAME in userDoc:
            del userDoc[USERDB.LAST_NAME]
        if update.effective_user.username is not None:
            userDoc[USERDB.USERNAME] = update.effective_user.username
        elif USERDB.USERNAME in userDoc:
            del userDoc[USERDB.USERNAME]
        # User has used bot in the meantime so he won't pay attention to that old "snoozed by..." message -> Remove this property from DB in order to save http requests!
        if USERDB.MSG_ID_LAST_SNOOZE_NOTIFICATION in userDoc:
            del userDoc[USERDB.MSG_ID_LAST_SNOOZE_NOTIFICATION]
        # Update DB
        self.couchdb[DATABASES.USERS].save(userDoc)
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
                userWhoSnoozed = self.getCurrentGlobalSnoozeUserID()
                menuText += '\n<b>' + SYMBOLS.WARNING + 'Benachrichtigungen deaktiviert bis: ' + formatTimestampToGermanDate(
                    self.getCurrentGlobalSnoozeTimestamp()) + ' (noch ' + getFormattedTimeDelta(self.getCurrentGlobalSnoozeTimestamp()) + ')</b>'
                menuText += '\nVon: ' + self.getMeaningfulUserTitleInContext(userWhoSnoozed, update.effective_user.id)
                if str(userWhoSnoozed) == str(update.effective_user.id):
                    menuText += "\n<b>Denk bitte dran, Bot Alarme und die Kamera beim Verlassen der Hütte wieder zu aktivieren!</b>"
                mainMenuKeyboard.append([InlineKeyboardButton('Benachrichtigungen für alle aktivieren', callback_data=CallbackVars.UNMUTE)])
            else:
                menuText += '\nhier kannst du Aktivitäten-Benachrichtigungen (Alarme) abschalten.'
                menuText += '\nAlle Bot User werden benachrichtigt wenn du einen der Snooze-Buttons drückst also lass\' bitte deinen Spieltrieb beiseite!'
                mainMenuKeyboard.append([InlineKeyboardButton('1 Stunde', callback_data=CallbackVars.MUTE_HOURS + '1'),
                                         InlineKeyboardButton('12 Stunden', callback_data=CallbackVars.MUTE_HOURS + '12')])
                mainMenuKeyboard.append([InlineKeyboardButton('24 Stunden', callback_data=CallbackVars.MUTE_HOURS + '24'),
                                         InlineKeyboardButton('48 Stunden', callback_data=CallbackVars.MUTE_HOURS + '48')])
            mainMenuKeyboard.append([InlineKeyboardButton(SYMBOLS.MEGAPHONE + 'Broadcast', callback_data=CallbackVars.SEND_BROADCAST)])
            mainMenuKeyboard.append([InlineKeyboardButton(SYMBOLS.WRENCH + 'Einstellungen', callback_data=CallbackVars.MENU_SETTINGS)])
            menuText += "\nLetzte Sensordaten vom " + formatDatetimeToGermanDate(self.alarmsystem.lastSensorUpdateServersideDatetime) + " (vor " + getFormattedTimeDelta(self.alarmsystem.lastSensorUpdateServersideDatetime.timestamp()) + "):<pre>"
            index = 0
            for sensor in list(self.alarmsystem.sensors.values()):
                if index > 0:
                    menuText += "\n"
                menuText += sensor.getName() + ": " + str(sensor.getValue()) + " | " + sensor.getStatusText()
                index += 1
            menuText += "</pre>"
            if self.userIsAdmin(update.effective_user.id):
                menuText += '\n' + SYMBOLS.CONFIRM + '<b>Du bist Admin!</b>'
                menuText += '\nMissbrauche deine Macht nicht!'
                mainMenuKeyboard.append([InlineKeyboardButton(SYMBOLS.FLASH + 'ACP', callback_data=CallbackVars.MENU_ACP)])
            menuText += "\n\n<i>antiBurglaryTelegramBot " + BOT_VERSION + " made with " + SYMBOLS.HEART + " and " + SYMBOLS.BEERS + " for Epi (2021)</i>"
            self.botEditOrSendNewMessage(update, context, menuText,
                                         reply_markup=InlineKeyboardMarkup(mainMenuKeyboard))
        return CallbackVars.MENU_MAIN

    def botAcpDisplayUserList(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        self.adminOrException(update.effective_user.id)
        users = self.getAllUsersExceptOne(update.effective_user.id)
        acpKeyboard = []
        if len(users) == 0:
            # Edge-case
            menuText = "<b>Es gibt außer dir noch keine weiteren Benutzer!</b>"
        else:
            for userIDStr in users:
                userPrefix = self.getUserRightsPrefix(userIDStr)
                acpKeyboard.append([InlineKeyboardButton(userPrefix + self.getMeaningfulUserTitle(userIDStr), callback_data=CallbackVars.MENU_ACP_ACTIONS + userIDStr)])
            menuText = "<b>Benutzerliste:</b>"
            menuText += "\nBenutzer werden nicht zwangsläufig über Änderungen informiert!"
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
            menuText += "\nTelegram Benutzer-ID: " + userIDStr
            menuText += "\nRegistriert am: " + formatTimestampToGermanDate(userDoc[USERDB.TIMESTAMP_REGISTERED])
            menuText += "\nBestätigt am: " + formatTimestampToGermanDate(userDoc[USERDB.TIMESTAMP_APPROVED])
            menuText += "\nBestätigt von: " + self.getMeaningfulUserTitle(userDoc[USERDB.APPROVED_BY])
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
            users = self.getApprovedUsersExceptOne(update.effective_user.id)
            logging.info("Sending messages to " + str(len(users)) + " users...")
            for userID in users:
                userDoc = self.getUserDoc(userID)
                msg = self.sendMessage(userID, text=self.getSnoozedUntilText(True))
                if msg is not None:
                    userDoc[USERDB.MSG_ID_LAST_SNOOZE_NOTIFICATION] = msg.message_id
                    self.couchdb[DATABASES.USERS].save(userDoc)
        else:
            logging.info("User attempted snooze but snooze is already active: " + str(update.effective_user.id))
        return self.botDisplayMenuMain(update, context)

    def getSnoozedUntilText(self, addETA: bool) -> str:
        text = SYMBOLS.WARNING + self.getMeaningfulUserTitle(self.getCurrentGlobalSnoozeUserID()) + " hat Benachrichtigungen deaktiviert bis: " + formatTimestampToGermanDate(
            self.getCurrentGlobalSnoozeTimestamp())
        if addETA:
            text += ' (noch ' + getFormattedTimeDelta(self.getCurrentGlobalSnoozeTimestamp()) + ')'
        text += '!'
        text += '\nMit /start siehst du den aktuellen Stand.'
        return text

    def botUnsnooze(self, update: Update, context: CallbackContext):
        """ Activates notifications for all users. """
        # Save global state
        botDoc = self.getBotDoc()
        baseText = ''
        if BOTDB.TIMESTAMP_SNOOZE_UNTIL in botDoc:
            baseText = self.getSnoozedUntilText(False)
            del botDoc[BOTDB.TIMESTAMP_SNOOZE_UNTIL]
        if BOTDB.MUTED_BY_USER_ID in botDoc:
            del botDoc[BOTDB.MUTED_BY_USER_ID]
        self.couchdb[DATABASES.BOTSTATE].save(botDoc)
        users = self.getApprovedUsersExceptOne(update.effective_user.id)
        logging.info("Editing snooze messages of " + str(len(users)) + " users...")
        # Edit "snoozed" message of all users for which this still is the last message in their message history with this bot!
        userTitle = self.getMeaningfulUserTitle(update.effective_user.id)
        for userID in users:
            userDoc = self.getUserDoc(userID)
            if USERDB.MSG_ID_LAST_SNOOZE_NOTIFICATION in userDoc:
                text = baseText + "\n<b>EDIT\nStummschaltung aufgehoben von: " + userTitle + "</b>"
                self.editMessage(userID, userDoc[USERDB.MSG_ID_LAST_SNOOZE_NOTIFICATION], text=text)
        return self.botDisplayMenuMain(update, context)

    def botApprovalAllow(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        self.adminOrException(update.effective_user.id)
        userIDStr = query.data.replace(CallbackVars.APPROVE_USER, "")
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None or userDoc.get(USERDB.IS_APPROVED, False):
            # This should never happen!
            self.botEditOrSendNewMessage(update, context, SYMBOLS.DENY + "Anfrage bereits bearbeitet!")
        else:
            self.botEditOrSendNewMessage(update, context, SYMBOLS.CONFIRM + "Benutzer bestätigt: " + self.getMeaningfulUserTitle(userIDStr))
            self.approveUser(userIDStr, str(update.effective_user.id))
        return ConversationHandler.END

    def botApprovalDeny(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        self.adminOrException(update.effective_user.id)
        userIDStr = query.data.replace(CallbackVars.DECLINE_USER, "")
        userDoc = self.getUserDoc(userIDStr)
        if userDoc is None:
            # This should never happen
            self.botEditOrSendNewMessage(update, context, SYMBOLS.DENY + "Anfrage bereits Admin bearbeitet!")
        else:
            self.botEditOrSendNewMessage(update, context, SYMBOLS.DENY + "Benutzer abgelehnt: " + self.getMeaningfulUserTitle(userIDStr))
            self.denyUser(userIDStr, update.effective_user.id)
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

    def approveUser(self, userID: Union[int, str], adminUserID: Union[int, str]) -> None:
        """
        :param userID: ID of the user to approve
        :param adminUserID: ID of the user that has approved the other user.
        :return: None
        """
        userID = str(userID)
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            logging.warning("User approval failed: userID doesn't exist in DB")
            return
        userDoc[USERDB.IS_APPROVED] = True
        userDoc[USERDB.APPROVED_BY] = adminUserID
        userDoc[USERDB.TIMESTAMP_APPROVED] = datetime.now().timestamp()
        # Inform user that he has been approved
        text = SYMBOLS.CONFIRM + "Du wurdest freigeschaltet!"
        text += "\nMit /start kommst du in das Hauptmenü."
        self.sendMessage(userID, text)
        # Update DB
        self.couchdb[DATABASES.USERS].save(userDoc)
        # Edit approval request messages of all other admins
        allOtherAdmins = self.getAdminsExceptOne(adminUserID)
        text = SYMBOLS.CONFIRM + self.getMeaningfulUserTitle(userID) + " wurde freigeschaltet von " + self.getMeaningfulUserTitle(adminUserID)
        for adminUserIDTmp in allOtherAdmins:
            adminDoc = self.getUserDoc(adminUserIDTmp)
            approvalRequestsMessageIDs = adminDoc.get(USERDB.MSG_IDS_APPROVAL_REQUESTS, {})
            if userID not in approvalRequestsMessageIDs:
                continue
            thisUserApprovalMessageID = approvalRequestsMessageIDs[userID]
            # Edit message accordingly
            self.editMessage(adminUserIDTmp, thisUserApprovalMessageID, text=text)
            # Update DB
            del approvalRequestsMessageIDs[userID]
            self.couchdb[DATABASES.USERS].save(adminDoc)

    def denyUser(self, userID: Union[int, str], adminUserID: Union[int, str]) -> None:
        """
        Denies- and deletes a user.
        :param userID: ID of the user to approve
        :param adminUserID: ID of the user that has approved the other user.
        :return: None
        """
        userID = str(userID)
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            logging.warning("User deny failed: userID doesn't exist in DB")
            return
        # Inform user that he has been denied
        if self.userIsApproved(userID):
            text = SYMBOLS.DENY + "Du wurdest gelöscht!"
        else:
            text = SYMBOLS.DENY + "Du wurdest abgelehnt!"
        self.sendMessage(userID, text)
        # Edit approval request messages of all other admins
        allOtherAdmins = self.getAdminsExceptOne(adminUserID)
        text = SYMBOLS.DENY + self.getMeaningfulUserTitle(userID) + " wurde abgelehnt/gelöscht von " + self.getMeaningfulUserTitle(adminUserID)
        for adminUserIDTmp in allOtherAdmins:
            adminDoc = self.getUserDoc(adminUserIDTmp)
            approvalRequestsMessageIDs = adminDoc.get(USERDB.MSG_IDS_APPROVAL_REQUESTS, {})
            if userID not in approvalRequestsMessageIDs:
                continue
            thisUserApprovalMessageID = approvalRequestsMessageIDs[userID]
            # Edit message accordingly
            self.editMessage(adminUserIDTmp, thisUserApprovalMessageID, text=text)
            # Update DB
            del approvalRequestsMessageIDs[userID]
            self.couchdb[DATABASES.USERS].save(adminDoc)
        del self.couchdb[DATABASES.USERS][userID]

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
                text += "\n<b>Gratulation! Du bist der erste User -> Admin!</b>"
            else:
                text += "\nWarte auf Freischaltung durch einen Admin."
                text += "\nDu wirst benachrichtigt, sobald dein Account freigeschaltet wurde."
                self.couchdb[DATABASES.USERS][str(update.effective_user.id)] = userData
                self.sendUserApprovalRequestToAllAdmins(update.effective_user.id)
            self.sendMessage(update.effective_message.chat_id, text)
            return CallbackVars.MENU_MAIN
        else:
            # User entered incorrect password
            self.sendMessage(chat_id=update.effective_message.chat_id, text=SYMBOLS.DENY + "Falsches Passwort!")
            return CallbackVars.MENU_ASK_FOR_PASSWORD

    def botDisplaySettings(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        text = SYMBOLS.WRENCH + "<b>Einstellungen</b>"
        settingsKeyboard = [
            [InlineKeyboardButton(SYMBOLS.INFORMATION + 'DSGVO Anfrage', callback_data=CallbackVars.MENU_SETTINGS_DISPLAY_OWN_DATA),
             InlineKeyboardButton(SYMBOLS.DENY + 'Account löschen', callback_data=CallbackVars.MENU_SETTINGS_DELETE_ACCOUNT)],
            [InlineKeyboardButton(SYMBOLS.BACK + 'Zurück', callback_data=CallbackVars.MENU_MAIN)]
        ]
        reply_markup = InlineKeyboardMarkup(settingsKeyboard)
        self.botEditOrSendNewMessage(update, context, text=text, reply_markup=reply_markup)
        return CallbackVars.MENU_SETTINGS

    def botDisplayOwnUserData(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        text = SYMBOLS.INFORMATION + "<b>Deine Auskunftsunterlagen nach Art. 15 DSGVO</b>"
        text += "<pre>"
        userDoc = self.getUserDoc(update.effective_user.id)
        userDoc[USERDB.TIMESTAMP_LAST_TIME_REQUESTED_DSGVO_DATA] = datetime.now().timestamp()
        self.couchdb[DATABASES.USERS].save(userDoc)
        for key, value in userDoc.items():
            text += "\n" + key + ": " + str(value)
        text += "</pre>"
        text += "\ntimestamp Werte = Datumsangaben -> Umrechenbar in lesbare Datumsangaben z.B. mit Webtool epochconverter.com (Werte ohne Nachkommastellen verwenden)"
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(SYMBOLS.BACK + 'Zurück', callback_data=CallbackVars.MENU_SETTINGS)]])
        self.botEditOrSendNewMessage(update, context, text=text, reply_markup=reply_markup, disable_web_page_preview=True)
        return CallbackVars.MENU_SETTINGS_DISPLAY_OWN_DATA

    def botDeleteOwnAccountSTART(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        text = SYMBOLS.DENY + "<b>Accountlöschung</b>"
        text += "\nAntworte mit deiner Telegram Benutzer-ID <b>" + str(update.effective_user.id) + "</b> um deinen Account zu löschen."
        text += "\nNach der Löschung wirst du den Bot ohne erneute Passworteingabe und Bestätigung nicht mehr verwenden können!!"
        text += "\nLöschung abbrechen mit /cancel"
        self.botEditOrSendNewMessage(update, context, text=text)
        return CallbackVars.MENU_SETTINGS_DELETE_ACCOUNT

    def botWTF(self, update: Update, context: CallbackContext):
        """
        Execute this whenever user enters nonsense.
        """
        text = SYMBOLS.WARNING + "<b>Manchmal sitzt das Problem vor dem Bildschirm!</b>"
        text += "\nLeider bin ich nur ein Bot und kann mit deiner letzten Eingabe nichts anfangen :("
        self.sendMessage(chat_id=update.effective_user.id, text=text)
        return CallbackVars.MENU_MAIN

    def botDeleteOwnAccount(self, update: Update, context: CallbackContext):
        if update.message.text != str(update.effective_user.id):
            text = SYMBOLS.DENY + "Falsche Antwort!"
            text += "\nLöschung abbrechen mit /cancel"
            self.sendMessage(chat_id=update.effective_user.id, text=text)
            return CallbackVars.MENU_SETTINGS_DELETE_ACCOUNT
        else:
            self.deleteUser(update.effective_user.id)
            self.sendMessage(chat_id=update.effective_user.id, text=SYMBOLS.CONFIRM + "Dein Account wurde gelöscht. Cya!")
            return ConversationHandler.END

    def botSendUserDefinedBroadcastSTART(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        text = SYMBOLS.MEGAPHONE + "Gib den zu sendenden Text ein."
        text += "\nDieser wird ohne weitere Bestätigung an alle Bot User geschickt!"
        text += "\nZurück ins Hauptmenü mit /start!"
        self.botEditOrSendNewMessage(update, context, text)
        return CallbackVars.SEND_BROADCAST

    def botSendUserDefinedBroadcast(self, update: Update, context: CallbackContext):
        recipients = self.getApprovedUsersExceptOne(update.effective_user.id)
        answerToUser = SYMBOLS.CONFIRM + "Nachricht an alle " + str(len(recipients)) + " Bot Nutzer gesendet gesendet!"
        answerToUser += "\nMit /start kommst du zurück in Hauptmenü."
        self.sendMessage(chat_id=update.effective_message.chat_id, text=answerToUser)
        userMessage = update.message.text
        text = "<b>Broadcast von " + self.getMeaningfulUserTitle(update.effective_user.id) + ":</b>"
        text += "\n" + userMessage
        self.sendMessageToMultipleUsers(recipients, text)
        userDoc = self.getUserDoc(update.effective_user.id)
        userDoc[USERDB.TIMESTAMP_LAST_BROADCAST_SENT] = datetime.now().timestamp()
        self.couchdb[DATABASES.USERS].save(userDoc)
        return ConversationHandler.END

    def botEditOrSendNewMessage(self, update: Update, context: CallbackContext, text: str,
                                reply_markup: ReplyMarkup = None, disable_web_page_preview=None):
        query = update.callback_query
        if query is not None:
            query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            context.bot.send_message(chat_id=update.effective_message.chat_id, reply_markup=reply_markup, text=text,
                                     parse_mode='HTML', disable_web_page_preview=disable_web_page_preview)

    def sendUserApprovalRequestToAllAdmins(self, userID: Union[int, str]) -> None:
        adminUsers = self.getAdmins()
        index = 0
        userDB = self.couchdb[DATABASES.USERS]
        userID = str(userID)
        userDoc = userDB[userID]
        menuText = 'Benutzer erbittet Freischaltung: ' + self.getMeaningfulUserTitle(userID)
        approvalKeyboard = [
            [InlineKeyboardButton(SYMBOLS.CONFIRM + 'Annehmen', callback_data=CallbackVars.APPROVE_USER + str(userID)),
             InlineKeyboardButton(SYMBOLS.DENY + 'Ablehnen/Löschen', callback_data=CallbackVars.DECLINE_USER + str(userID))]
        ]
        reply_markup = InlineKeyboardMarkup(approvalKeyboard)
        for adminUserID in adminUsers:
            print("Sending approval requests to admin " + str((index + 1)) + " / " + str(len(adminUsers)))
            approvalMsg = self.sendMessage(adminUserID, menuText, reply_markup=reply_markup)
            # Update DB and save that messageID -> We need that later!
            adminUserDoc = adminUsers[adminUserID]
            approvalMessageIDs = adminUserDoc.get("", {})
            approvalMessageIDs[userID] = approvalMsg.message_id
            adminUserDoc[USERDB.MSG_IDS_APPROVAL_REQUESTS] = approvalMessageIDs
            userDB.save(adminUserDoc)
            index += 1
        userDoc[USERDB.APPROVAL_REQUEST_HAS_BEEN_SENT] = True
        userDB.save(userDoc)

    def sendAlarmNotifications(self):
        self.alarmsystem.updateAlarms()

        totalAdminOnlyAlarmText = ""
        totalUserAlarmText = ""
        if self.isGloballySnoozed():
            # Collect all alarms that should even be sent in snoozed mode
            amdinOnlyAlarmTextSnoozeOverride = self.alarmsystem.getAlarmTextAdminOnlySnoozeOverride()
            if amdinOnlyAlarmTextSnoozeOverride is not None:
                totalAdminOnlyAlarmText += "Admin Alarme Snooze Override:"
                totalAdminOnlyAlarmText += "\n" + amdinOnlyAlarmTextSnoozeOverride
            alarmsSnoozeOverride = self.alarmsystem.getAlarmTextSnoozeOverride()
            if alarmsSnoozeOverride is not None:
                totalUserAlarmText += "User Alarme Snooze Override:"
                totalUserAlarmText += "\n" + alarmsSnoozeOverride
        else:
            # Collect all alarms
            adminAlarms = self.alarmsystem.getAlarmTextAdminOnly()
            if adminAlarms is not None:
                if len(totalAdminOnlyAlarmText) > 0:
                    totalAdminOnlyAlarmText += "\n"
                totalAdminOnlyAlarmText += "Admin Alarme:"
                totalAdminOnlyAlarmText += "\n" + adminAlarms
            userAlarms = self.alarmsystem.getAlarmText()
            if userAlarms is not None:
                if len(totalUserAlarmText) > 0:
                    totalUserAlarmText += "\n"
                totalUserAlarmText += "User Alarme:"
                totalUserAlarmText += "\n" + userAlarms
        # Send alarms if there are some
        if len(totalAdminOnlyAlarmText) > 0:
            logging.warning("Sending out admin alarms...")
            self.sendMessageToAllAdmins(totalAdminOnlyAlarmText)
        if len(totalUserAlarmText) > 0:
            logging.warning("Sending out user alarms...")
            self.sendMessageToAllApprovedUsers(totalAdminOnlyAlarmText)

        # if not self.isGloballySnoozed():
        #     amdinOnlyAlarmText = self.alarmsystem.getAlarmTextAdminOnly()
        #     if amdinOnlyAlarmText is not None:
        #         logging.warning("Sending out admin alarms...")
        #         totalAdminOnlyAlarmText = "Admin Alarme"
        #         totalAdminOnlyAlarmText += "\n" + amdinOnlyAlarmText
        #         self.sendMessageToAllAdmins(amdinOnlyAlarmText)
        #     else:
        #         text = "<b>Alarm! " + self.alarmsystem.channelName + "</b>"
        #         for alarmMsg in self.alarmsystem.alarms:
        #             text += "\n" + alarmMsg
        #         self.sendMessageToAllApprovedUsers(text)
        # elif len(self.alarmsystem.alarmsSnoozeOverride) > 0:
        #     # Some alarms should be sent even in snoozed mode
        #     text = "<b>Alarm! " + self.alarmsystem.channelName + "</b>"
        #     for alarmMsg in self.alarmsystem.alarmsSnoozeOverride:
        #         text += "\n" + alarmMsg
        #     self.sendMessageToAllApprovedUsers(text)

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

    def sendMessageToAllAdmins(self, text: str):
        adminUsers = self.getAdmins()
        self.sendMessageToMultipleUsers(adminUsers, text)

    def sendMessageToMultipleUsers(self, users: dict, text: str):
        logging.info("Sending messages to " + str(len(users)) + " users...")
        for userID in users:
            self.sendMessage(userID, text)

    def sendMessage(self, chat_id: Union[int, str], text: str, reply_markup=None) -> Union[None, Message]:
        try:
            return self.updater.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
        except BadRequest:
            pass
        except Unauthorized:
            # E.g. user has blocked bot -> Save that so we can remove such users on DB cleanup
            userDoc = self.getUserDoc(chat_id)
            if userDoc is not None:
                userDoc[USERDB.TIMESTAMP_LAST_BLOCKED_BOT_ERROR] = datetime.now().timestamp()
                self.couchdb[DATABASES.USERS].save(userDoc)
            pass

    def editMessage(self, chat_id: Union[int, str], message_id: int, text: str) -> Union[None, Message]:
        try:
            return self.updater.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode='HTML')
        except BadRequest:
            traceback.print_exc()
            pass
        except Unauthorized:
            # E.g. user has blocked bot -> Save that so we can remove such users on DB cleanup
            userDoc = self.getUserDoc(chat_id)
            if userDoc is not None:
                userDoc[USERDB.TIMESTAMP_LAST_BLOCKED_BOT_ERROR] = datetime.now().timestamp()
                self.couchdb[DATABASES.USERS].save(userDoc)
            pass

    def getMeaningfulUserTitle(self, userID: Union[int, str]) -> str:
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

    def getMeaningfulUserTitleInContext(self, targetUserID: Union[int, str], ownUserID: Union[int, str]):
        if str(ownUserID) == str(targetUserID):
            return "dir"
        else:
            return self.getMeaningfulUserTitle(targetUserID)


    def getAdmins(self) -> dict:
        admins = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN):
                admins[userID] = userDoc
        return admins

    def getAdminsExceptOne(self, ignoreUserID: Union[int, str]) -> dict:
        """ Returns approved users and admins. """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        ignoreUserID = str(ignoreUserID)
        for userID in userDB:
            if userID == ignoreUserID:
                continue
            elif not self.userIsAdmin(userID):  # Skip all non-admins
                continue
            userDoc = userDB[userID]
            users[userID] = userDoc
        return users

    def getApprovedUsers(self) -> dict:
        """ Returns approved users and admins. """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False):
                users[userID] = userDoc
        return users

    def getApprovedUsersExceptOne(self, ignoreUserID: Union[int, str]) -> dict:
        """ Returns approved users and admins. """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        ignoreUserID = str(ignoreUserID)
        for userID in userDB:
            if userID == ignoreUserID:
                continue
            userDoc = userDB[userID]
            if userDoc.get(USERDB.IS_ADMIN) or userDoc.get(USERDB.IS_APPROVED, False):
                users[userID] = userDoc
        return users

    def getAllUsersExceptOne(self, ignoreUserID: Union[int, str]) -> dict:
        """ Returns ALL users and admins. """
        users = {}
        userDB = self.couchdb[DATABASES.USERS]
        ignoreUserID = str(ignoreUserID)
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

    def userTriggerAdmin(self, userID: Union[int, str]):
        userDoc = self.getUserDoc(userID)
        if userDoc is None:
            return
        elif userDoc.get(USERDB.IS_ADMIN, False):
            userDoc[USERDB.IS_ADMIN] = False
            self.couchdb[DATABASES.USERS].save(userDoc)
        else:
            userDoc[USERDB.IS_ADMIN] = True
            self.couchdb[DATABASES.USERS].save(userDoc)

    def deleteUser(self, userID: Union[int, str]) -> bool:
        """ Deletes a user from DB. """
        if str(userID) in self.couchdb[DATABASES.USERS]:
            del self.couchdb[DATABASES.USERS][str(userID)]
            return True
        else:
            return False

    def botAcpUserDelete(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        self.adminOrException(update.effective_user.id)
        userIDStr = query.data.replace(CallbackVars.MENU_ACP_ACTION_DELETE_USER, "")
        self.denyUser(userIDStr, update.effective_user.id)
        return self.botAcpDisplayUserList(update, context)

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
