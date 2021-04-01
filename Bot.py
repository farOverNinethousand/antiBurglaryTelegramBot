import logging
import time
from datetime import datetime

import couchdb
import schedule
from telegram import Update, ReplyMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, ConversationHandler, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, Filters

from Helper import Config, loadConfig


class CallbackVars:
    MENU_MAIN = 'MENU_MAIN'
    MENU_ASK_FOR_PASSWORD = 'MENU_ASK_FOR_PASSWORD'
    MUTE_HOURS_1 = 'MUTE_HOURS_1'
    MUTE_HOURS_12 = 'MUTE_HOURS_12'
    MUTE_HOURS_24 = 'MUTE_HOURS_24'
    MUTE_HOURS_48 = 'MUTE_HOURS_48'
    UNMUTE = 'UNMUTE'
    MUTE_SELECTION = 'MUTE_SELECTION'


class DATABASES:
    USERS = 'users'


class USERDB:
    USERNAME = 'username'
    FIRST_NAME = 'first_name'
    LAST_NAME = 'last_name'
    IS_APPROVED = 'is_approved'
    SNOOZE_UNTIL_TIMESTAMP = 'snooze_until_timestamp'


class ABBot:

    def __init__(self):
        self.cfg = loadConfig()
        if self.cfg is None or self.cfg.get(Config.DB_URL) is None:
            raise Exception('Broken config')
        """ Init DB """
        self.couchdb = couchdb.Server(self.cfg[Config.DB_URL])
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
                ],
                CallbackVars.MENU_ASK_FOR_PASSWORD: [
                     CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MENU_MAIN + '$'),
                     MessageHandler(Filters.text, self.botCheckPassword),
                ],
                CallbackVars.MUTE_SELECTION: [
                    # TODO
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MUTE_HOURS_1 + '$'),
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MUTE_HOURS_12 + '$'),
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MUTE_HOURS_24 + '$'),
                    CallbackQueryHandler(self.botDisplayMenuMain, pattern='^' + CallbackVars.MUTE_HOURS_48 + '$'),
                ]
            },
            fallbacks=[CommandHandler('start', self.botDisplayMenuMain)],
            name="MainConversationHandler",
        )
        dispatcher.add_handler(conv_handler)
        # conv_handler2 = ConversationHandler(
        #     entry_points=[CommandHandler('tschau', self.botUserDeleteSTART)],
        #     states={
        #         CallbackVars.MENU_SETTINGS_USER_DELETE_DATA: [
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
            menuText = 'Hallo ' + update.effective_user.first_name + ',\n'
            userDoc = self.couchdb[DATABASES.USERS][(str(update.effective_user.id))]
            if userDoc.get(USERDB.SNOOZE_UNTIL_TIMESTAMP, 0) > datetime.now().timestamp():
                menuText += '\nBenachrichtigungen sind noch deaktiviert für:'
                unmuteKeyboard = [[InlineKeyboardButton('Benachrichtigungen aktivieren', callback_data=CallbackVars.UNMUTE)]]
                self.botEditOrSendNewMessage(update, context, menuText, reply_markup=InlineKeyboardMarkup(unmuteKeyboard))
            else:
                # Cleanup DB
                if USERDB.SNOOZE_UNTIL_TIMESTAMP in userDoc:
                    del userDoc[USERDB.SNOOZE_UNTIL_TIMESTAMP]
                    self.couchdb[DATABASES.USERS].save(userDoc)
                menuText += '\nHier kannst du Aktivitäten-Benachrichtigungen abschalten:'
                snoozeKeyboard = [
                    [InlineKeyboardButton('1 Stunde', callback_data=CallbackVars.MUTE_HOURS_1), InlineKeyboardButton('12 Stunden', callback_data=CallbackVars.MUTE_HOURS_12)],
                    [InlineKeyboardButton('24 Stunde', callback_data=CallbackVars.MUTE_HOURS_24), InlineKeyboardButton('48 Stunden', callback_data=CallbackVars.MUTE_HOURS_48)]
                    ]
                self.botEditOrSendNewMessage(update, context, menuText, reply_markup=InlineKeyboardMarkup(snoozeKeyboard))
            return CallbackVars.MUTE_SELECTION

    def botCheckPassword(self, update: Update, context: CallbackContext):
        user_input = update.message.text
        if user_input == self.cfg[Config.BOT_PASSWORD]:
            # Update DB
            self.couchdb[DATABASES.USERS][str(update.effective_user.id)] = {
                USERDB.USERNAME: update.effective_user.username,
                USERDB.FIRST_NAME: update.effective_user.first_name,
                USERDB.LAST_NAME: update.effective_user.last_name
            }
            context.bot.send_message(chat_id=update.effective_message.chat_id, text="Korrektes Passwort!", parse_mode='HTML')
            return self.botDisplayMenuMain(update, context)
        else:
            context.bot.send_message(chat_id=update.effective_message.chat_id, text="Falsches Passwort!", parse_mode='HTML')
            return CallbackVars.MENU_ASK_FOR_PASSWORD

    def botEditOrSendNewMessage(self, update: Update, context: CallbackContext, text: str, reply_markup: ReplyMarkup = None):
        query = update.callback_query
        if query is not None:
            query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            context.bot.send_message(chat_id=update.effective_message.chat_id, reply_markup=reply_markup, text=text, parse_mode='HTML')


    def updateNotifications(self):
        usersToApprove = []
        userDB = self.couchdb[DATABASES.USERS]
        for userID in userDB:
            userDoc = userDB[userID]
            if not userDoc.get(USERDB.IS_APPROVED, False):
                usersToApprove.append(userDoc)
        logging.info("Number of users who need approval: " + str(len(usersToApprove)))
        # TODO: Send notifications to admins so they can approve these users
        index = 0
        for userDoc in usersToApprove:
            print("Sending notification " + str((index + 1)) + " / " + str(len(usersToApprove)))


if __name__ == '__main__':
    bot = ABBot()
    bot.updater.start_polling()
    schedule.every(5).seconds.do(bot.updateNotifications)
    while True:
        schedule.run_pending()
        time.sleep(1)
