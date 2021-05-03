import logging
from datetime import datetime
from json import loads

from hyper import HTTP20Connection

from Helper import Config, formatDatetimeToGermanDate, SYMBOLS
from Sensor import Sensor


class AlarmSystem:

    def __init__(self, config: dict):
        self.cfg = config
        self.lastAlarmSentTimestamp = -1
        self.lastEntryIDChangeTimestamp = -1
        self.lastTimeNoNewSensorDataAvailableWarningSentTimestamp = -1
        self.lastSensorUpdateDatetime = datetime.now()
        self.lastFieldIDToSensorsMapping = {}
        self.noDataWarningIntervalSeconds = 600
        self.alarms = []
        self.lastEntryID = -1

    def getSensorAPIResponse(self) -> dict:
        # https://community.thingspeak.com/documentation%20.../api/
        conn = HTTP20Connection('api.thingspeak.com')
        conn.request("GET", '/channels/' + str(self.cfg[Config.THINGSPEAK_CHANNEL]) + '/feed.json?key=' + self.cfg[Config.THINGSPEAK_READ_APIKEY] + '&offset=1')
        apiResult = loads(conn.get_response().read())
        return apiResult

    def getAlarms(self) -> list:
        # TODO
        return self.alarms

    def setNoDataWarningIntervalMinutes(self, minutes: int):
        self.noDataWarningIntervalSeconds = minutes * 60

    def updateSensorStates(self):
        """ Updates sensor states and saves/sets resulting alarms """
        # Clear last list of alarms
        self.alarms = []
        apiResult = self.getSensorAPIResponse()
        channelInfo = apiResult['channel']
        sensorResults = apiResult['feeds']
        # Most of all times we want to check only new entries but if e.g. the channel gets reset we need to check entries lower than our last saved number!
        checkOnlyHigherEntryIDs = True
        currentLastEntryID = channelInfo['last_entry_id']
        if self.lastEntryID == -1:
            # E.g. first time fetching data
            self.lastEntryID = currentLastEntryID
        elif currentLastEntryID < self.lastEntryID:
            # Rare case
            checkOnlyHigherEntryIDs = False
            logging.info("Thingspeak channel has been reset(?) -> Checking ALL entries")
        else:
            logging.info("Checking all entries > " + str(self.lastEntryID))
            pass
        fieldIDToSensorMapping = {}
        for fieldIDStr, sensorUserConfig in self.cfg[Config.THINGSPEAK_FIELDS_ALARM_STATE_MAPPING].items():
            fieldKey = 'field' + fieldIDStr
            if fieldKey not in channelInfo:
                logging.info("Detected unconfigured field: " + fieldKey)
                continue
            fieldIDToSensorMapping[int(fieldIDStr)] = Sensor(name=channelInfo[fieldKey], triggerValue=sensorUserConfig['trigger'], triggerOperator=sensorUserConfig['operator'],
                                                             alarmOnlyOnceUntilUntriggered=sensorUserConfig.get('alarmOnlyOnceUntilUntriggered', False))
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
                # 2021-04-18: Thingspeak sends all values as String though we expect float or int
                fieldValueRaw = feed[fieldKey]
                if '.' in fieldValueRaw:
                    sensor.setValue(float(fieldValueRaw))
                else:
                    sensor.setValue(int(fieldValueRaw))
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

        if currentLastEntryID == self.lastEntryID:
            logging.info(" --> No new data available --> Last data is from: " + formatDatetimeToGermanDate(
                self.lastSensorUpdateDatetime) + " [" + str(self.lastEntryID) + "]")
            if datetime.now().timestamp() - self.lastEntryIDChangeTimestamp >= self.noDataWarningIntervalSeconds:
                # Check if our alarm system maybe hasn't been responding for a long amount of time
                lastSensorDataIsFromDate = formatDatetimeToGermanDate(self.lastSensorUpdateDatetime)
                logging.warning("Got no new sensor data for a long time! Last data is from: " + lastSensorDataIsFromDate)
                if datetime.now().timestamp() - self.lastTimeNoNewSensorDataAvailableWarningSentTimestamp > 60 * 60:
                    self.alarms.append(SYMBOLS.DENY + "<b>Fehler Alarmanlage!Keine neuen Daten verfügbar!\nLetzte Sensordaten vom: " + lastSensorDataIsFromDate + "</b>")
            return
        elif len(alarmSensorsNames) > 0:
            print("Alarms triggered: " + formatDatetimeToGermanDate(alarmDatetime) + " | " + ', '.join(alarmSensorsNames))
            if datetime.now().timestamp() < (self.lastAlarmSentTimestamp + 1 * 60):
                # Only allow alarms every X minutes otherwise we'd send new messages every time this code gets executed!
                logging.info("Not sending alarms because: Flood protection")
            else:
                logging.warning("Sending out alarms...")
                # text = "<b>Alarm! " + channelInfo['name'] + "</b>"
                self.alarms.append('\n' + formatDatetimeToGermanDate(alarmDatetime) + ' | Sensoren: ' + ', '.join(alarmSensorsNames))
                self.lastAlarmSentTimestamp = datetime.now().timestamp()

        self.lastFieldIDToSensorsMapping = fieldIDToSensorMapping.copy()
        self.lastEntryID = currentLastEntryID
        self.lastEntryIDChangeTimestamp = datetime.now().timestamp()
