import time

import schedule
from telegram import Update
from telegram.ext import Updater, ConversationHandler, CommandHandler, CallbackContext, CallbackQueryHandler

from Helper import Config


class CallbackVars:
    MENU_MAIN = 'menu_main'


class ABBot:

    def __init__(self):
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

    def botDisplayMenuMain(self, update: Update, context: CallbackContext):
        query = update.callback_query
        if query is not None:
            query.answer()
        menuText = 'Hallo ' + update.effective_user.first_name + ', <b>Bock auf Einbruchschutz?</b>\n'
        if query is not None:
            query.edit_message_text(text=menuText, parse_mode='HTML')
        else:
            context.bot.send_message(chat_id=update.effective_message.chat_id, text=menuText, parse_mode='HTML')
        return CallbackVars.MENU_MAIN


def updateNotifications():
    pass


if __name__ == '__main__':
    bot = ABBot()
    bot.updater.start_polling()
    schedule.every(5).seconds.do(updateNotifications)
    while True:
        schedule.run_pending()
        time.sleep(1)
