# antiBurglaryTelegramBot
Arduino + Sensors + thingspeak.com API + Telegram Bot

# Was kann dieses Projekt?
* Daten von einfachen Sensoren, die auf [thingspeak.com](https://thingspeak.com/) hochgeladen wurden simpel auswerten und bei Bedarf Alarme an alle Benutzer des eingerichteten Telegram Bots schicken.
* Der Telegram Bot unterscheidet zwischen User und Admin und hat eine simple Benutzerverwaltung
* Alle Benutzer können die Bot Alarme deaktivieren, was wiederum alle benachrichtigt

## Wo kam dieses Projekt zum Einsatz?
Überwachung eines privaten Waldgrundstückes.

# Screenshots / Anleitung  
1. Bot anschreiben, Passwort eingeben und auf Bestätigung warten:  
![Registrierung](https://raw.githubusercontent.com/farOverNinethousand/antiBurglaryTelegramBot/main/Screenshots/Screen_0.png "Registrierung")  
2. Bot verwenden:  
Hauptmenü (Befehl `/start`) - Alarme aktiv:  
![Hauptmenü](https://raw.githubusercontent.com/farOverNinethousand/antiBurglaryTelegramBot/main/Screenshots/Screen_1.png "Hauptmenü")  
Mit den "X Stunden" Buttons kannst du Benachrichtigungen/Alarme für alle Bot-Benutzer deaktivieren.  
Hauptmenü (Befehl `/start`) - Alarme inaktiv:  
![Hauptmenü2](https://raw.githubusercontent.com/farOverNinethousand/antiBurglaryTelegramBot/main/Screenshots/Screen_1.1.png "Hauptmenü2")  
Wurden Alarme deaktiviert, ändert sich das Menü und man kann sie jederzeit wieder aktivieren.  
ACP (**A**dmin**C**ontrol**P**anel)  Userliste  
![AdminControlPanel](https://raw.githubusercontent.com/farOverNinethousand/antiBurglaryTelegramBot/main/Screenshots/Screen_2.png "AdminControlPanel")  
ACP (**A**dmin**C**ontrol**P**anel)  User Aktionen  
![AdminControlPanel User Aktionen](https://raw.githubusercontent.com/farOverNinethousand/antiBurglaryTelegramBot/main/Screenshots/Screen_3.png "AdminControlPanel User Aktionen")  
Admins sollten wissen was sie tun daher gibt es keine Anleitung für diesen Teil.

# TODOs
* Irgendwas findet man immer :D
* Refactoring der Alarm Funktion

# Installation
1. ``git clone diesesProjekt``
2. ``apt install python3-pip``
3. ``pip3 install -r requirements.txt``
4. [CouchDB](https://linuxize.com/post/how-to-install-couchdb-on-ubuntu-20-04/) installieren und einrichten.  
5. `config.json.default` in `config.json` umbenennen und eigene Daten eintragen (siehe unten).
6. Beim ersten Start- und erfolgreicher Passworteingabe ist der erste Benutzer automatisch ein Admin.

# Config Erklärung  
Key | Datentyp | Beschreibung | Beispiel
--- | --- | --- | ---
bot_token | String | Bot Token | `1234567890:HJDH-gh56urj6r5u6grhrkJO7Qw`
db_url | String | URL zur CouchDB Datenbank mitsamt Zugangsdaten | `http://username:pw@localhost:5984/`
bot_name | String | Name des Bots | `MyAntiBurglaryBot`
bot_password | String | Passwort, das User benötigen, um den Bot verwenden zu können. | `123456ABCabc`
thingspeak_channel | int | Thingspeak.com channelID | `123456`
thingspeak_read_apikey | String | Thingspeak.com read apikey | `FFFFGGGGHHHHTJLK`
thingspeak_fields_alarm_state_mapping | Map | Mapping für Sensordaten | `---`
thingspeak_fields_alarm_state_mapping[name] | String | Name des Sensors | `Test`
thingspeak_fields_alarm_state_mapping[trigger] | float | Ab welchem Wert soll dieser Sensor als getriggert gelten? | `3.15`
thingspeak_fields_alarm_state_mapping[operator] | String | Operator für den Triggerwert | `LESS`, `MORE`, `EQ`
thingspeak_fields_alarm_state_mapping[alarmOnlyOnceUntilUntriggered] | boolean  [Optional]  default=false | Ist dies ein Schwellwertsensor, der nach dem ersten Triggern nur einen Alarm auslösen darf bis er wieder nicht mehr getriggert ist?  Beispiel: Nur eine Warnung bei niedrigem Akkustand bis dieser wieder 'hoch' ist. | `true`

# Beispiel Config (config.json.default)

```
{
  "bot_token": "YourBotToken",
  "db_url": "http://TestUser:TestPW@localhost:5984/",
  "bot_name": "ExampleBot",
  "bot_password": "Test123456",
  "thingspeak_channel": 123456,
  "thingspeak_read_apikey": "BLA",
  "thingspeak_fields_alarm_state_mapping": {
    "1": {
      "name": "Door",
      "trigger": 1,
      "operator": "EQ",
      "alarmOnlyOnceUntilUntriggered": false
    },
    "2": {
      "name": "Movement",
      "trigger": 1,
      "operator": "EQ",
      "alarmOnlyOnceUntilUntriggered": false
    },
    "3": {
      "name": "BatteryVoltage",
      "trigger": 11.5,
      "operator": "LESS",
      "alarmOnlyOnceUntilUntriggered": true
    }
  }
}
```

**Diese Config tut folgendes:**  
1. Alarm wenn Sensor des thingspeak.com Feldes "field1" den Wert "1" hat.
2. Alarm wenn "field2" den Wert "1" hat.
3. Alarm, wenn "field3" einen Wert unter "11.5" hat.  
Dieser Alarm passiert jeweils nur 1x bis der Sensor nicht mehr getriggert ist. 
   In diesem Beispiel ist es eine Batteriespannung - sobald sie wieder über 11.5 Volt steigt wird dieser Sensor "enttriggert" (naja Schwellwert eben) und es darf ein neuer Alarm kommen, wenn die Spannung wieder abfällt.
   
# Bot Beschreibung
```
Epi Sicherheitssystem
Anleitung: github.com/farOverNinethousand/antiBurglaryTelegramBot#screenshots--anleitung
```

# Bot About
```
Epi Sicherheitssystem
Anleitung: github.com/farOverNinethousand/antiBurglaryTelegramBot#screenshots--anleitung
```

# Bot Commands Liste
```
start - Hauptmenü
cancel - 🚫Abbrechen
```
   
# Hardware
Die Hardwareseite dieses Projektes findet sich [HIER](https://github.com/Kaistee93/AlarmSystem_ESP8266).