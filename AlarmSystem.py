import logging
from datetime import datetime
from json import loads

from hyper import HTTP20Connection

from Helper import Config, formatDatetimeToGermanDate, SYMBOLS
from Sensor import Sensor


class AlarmSystem:

    def __init__(self, config: dict):
        self.cfg = config
        self.lastSensorAlarmSentTimestamp = -1
        self.sensorAlarmIntervalSeconds = 60
        self.lastEntryIDChangeTimestamp = -1
        self.lastSensorUpdateServersideDatetime = datetime.now()
        # Init sensors we want to check later
        self.sensors = {}
        for fieldIDStr, sensorUserConfig in self.cfg[Config.THINGSPEAK_FIELDS_ALARM_STATE_MAPPING].items():
            self.sensors[int(fieldIDStr)] = Sensor(name=sensorUserConfig['name'], triggerValue=sensorUserConfig['trigger'],
                                                   triggerOperator=sensorUserConfig['operator'],
                                                   alarmOnlyOnceUntilUntriggered=sensorUserConfig.get('alarmOnlyOnceUntilUntriggered', False))
        # Vars for "no data" warning
        self.noDataAlarmIntervalSeconds = 600
        self.lastNoNewSensorDataAvailableAlarmSentTimestamp = -1
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

    def setAlarmIntervalNoData(self, minutes: int):
        self.noDataAlarmIntervalSeconds = minutes * 60

    def setAlarmIntervalSensors(self, minutes: int):
        self.sensorAlarmIntervalSeconds = minutes * 60

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
        elif currentLastEntryID == self.lastEntryID:
            logging.info(" --> No new data available --> Last data is from: " + formatDatetimeToGermanDate(
                self.lastSensorUpdateServersideDatetime) + " [" + str(self.lastEntryID) + "]")
            if datetime.now().timestamp() - self.lastEntryIDChangeTimestamp >= self.noDataAlarmIntervalSeconds:
                # Check if our alarm system maybe hasn't been responding for a long amount of time
                lastSensorDataIsFromDate = formatDatetimeToGermanDate(self.lastSensorUpdateServersideDatetime)
                logging.warning("Got no new sensor data for a long time! Last data is from: " + lastSensorDataIsFromDate)
                if datetime.now().timestamp() - self.lastNoNewSensorDataAvailableAlarmSentTimestamp > 60 * 60:
                    self.alarms.append(SYMBOLS.DENY + "<b>Fehler Alarmanlage!Keine neuen Daten verfügbar!\nLetzte Sensordaten vom: " + lastSensorDataIsFromDate + "</b>")
            return
        elif currentLastEntryID < self.lastEntryID:
            # Rare case
            checkOnlyHigherEntryIDs = False
            logging.info("Thingspeak channel has been reset(?) -> Checking ALL entries")
        else:
            logging.info("Checking all entries > " + str(self.lastEntryID))
            pass
        alarmDatetime = None
        triggeredSensors = []
        alarmSensorsNames = []
        for feed in sensorResults:
            # Check all fields for which we got alarm state mapping
            entryID = feed['entry_id']
            if entryID <= self.lastEntryID and checkOnlyHigherEntryIDs:
                # Ignore entries we've checked before
                continue
            for fieldID, sensor in self.sensors.items():
                fieldKey = 'field' + str(fieldID)
                if fieldKey not in feed:
                    logging.warning("One of your configured sensors is not available in feed: " + fieldKey + " | " + sensor.getName())
                    continue
                sensorWasTriggeredBefore = sensor.isTriggered()
                # 2021-04-18: Thingspeak sends all values as String though we expect float or int
                fieldValueRaw = feed[fieldKey]
                if '.' in fieldValueRaw:
                    sensor.setValue(float(fieldValueRaw))
                else:
                    sensor.setValue(int(fieldValueRaw))
                thisDatetime = datetime.strptime(feed['created_at'], '%Y-%m-%dT%H:%M:%S%z')
                self.lastSensorUpdateServersideDatetime = thisDatetime
                # Check if alarm state is given
                if sensor.isTriggered():
                    if sensor.isAlarmOnlyOnceUntilUntriggered() and sensorWasTriggeredBefore:
                        # print("Ignore " + sensor.getName() + " because alarm is only allowed once until untriggered")
                        logging.info("Ignore " + sensor.getName() + " because alarm is only allowed once until untriggered")
                        continue
                    if sensor.getName() not in alarmSensorsNames:
                        alarmSensorsNames.append(sensor.getName())
                        triggeredSensors.append(sensor)
                        alarmDatetime = thisDatetime

        if len(alarmSensorsNames) > 0:
            print("Alarms triggered: " + formatDatetimeToGermanDate(alarmDatetime) + " | " + ', '.join(alarmSensorsNames))
            if datetime.now().timestamp() < (self.lastSensorAlarmSentTimestamp + self.sensorAlarmIntervalSeconds):
                # Only allow alarms every X minutes otherwise we'd send new messages every time this code gets executed!
                logging.info("Not sending alarms because: Flood protection")
            else:
                logging.warning("Sending out alarms...")
                # text = "<b>Alarm! " + channelInfo['name'] + "</b>"
                self.alarms.append('\n' + formatDatetimeToGermanDate(alarmDatetime) + ' | Sensoren: ' + ', '.join(alarmSensorsNames))
                self.lastSensorAlarmSentTimestamp = datetime.now().timestamp()

        self.lastEntryID = currentLastEntryID
        self.lastEntryIDChangeTimestamp = datetime.now().timestamp()
