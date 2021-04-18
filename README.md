# antiBurglaryTelegramBot
Arduino + Light Barriers + thingspeak.com API + Telegram Bot

# TODOs
* Irgendwas findet man immer :D

# Installation
1. ``git clone doesesProjekt``
2. ``apt install python3-pip``
3. ``pip3 install -r requirements.txt``
4. [CouchDB](https://linuxize.com/post/how-to-install-couchdb-on-ubuntu-20-04/) installieren und einrichten.  
5. `config.json.default` in `config.json` umbenennen und eigene Daten eintragen (siehe unten).

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
thingspeak_fields_alarm_state_mapping[operator] | String | Operator für den Triggerwert | `LESS, MORE, EQ`
thingspeak_fields_alarm_state_mapping[alarmOnlyOnceUntilUntriggered] | boolean | Ist dies ein Schwellwertsensor, der nach dem ersten Triggern für einen Alarm aktiv ist bis er nicht mehr getriggert ist? | `true`

# Beispiel Config (config.json.defaut)

```
{
  "bot_token": "YourBotToken",
  "db_url": "http://TestUser:TestPW@localhost:5984/",
  "public_channel_name": "ExampleChannel",
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

Diese Config tut folgendes:  
1. Alarm wenn Sensor des thingspeak.com Feldes "field1" den Wert "1" hat.
2. Alarm wenn "field2" den Wert "1" hat.
3. Alarm, wenn "field3" einen Wert unter "11.5" hat.  
Dieser Alarm passiert jeweils nur 1x bis der Sensor nicht mehr getriggert ist. 
   In diesem Beispiel ist es eine Batteriespannung - sobald sie wieder über 11.5 Volt steigt wird dieser Sensor "enttriggert" (naja Schwellwert eben) und es darf ein neuer Alarm kommen, wenn die Spannung wieder abfällt.
   
# Hardware
Die Hardwareseite dieses Projektes findet sich [HIER](https://github.com/Kaistee93/AlarmSystem_ESP8266).  
Das Projekt kann grundsätzlich mit anderen 