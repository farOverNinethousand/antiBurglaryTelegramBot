import logging
from datetime import datetime
from json import loads
from typing import Union

from hyper import HTTP20Connection

from Helper import Config, formatDatetimeToGermanDate, SYMBOLS
from Sensor import Sensor, SensorConfig


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
            self.sensors[int(fieldIDStr)] = Sensor(SensorConfig(name=sensorUserConfig['name'], triggerValue=sensorUserConfig['trigger'],
                                                   triggerOperator=sensorUserConfig['operator'],
                                                   alarmOnlyOnceUntilUntriggered=sensorUserConfig.get('alarmOnlyOnceUntilUntriggered', False),
                                                                triggeredText=sensorUserConfig.get('triggeredText', None),
                                                                unTriggeredText=sensorUserConfig.get('unTriggeredText', None),
                                                                overridesSnooze=sensorUserConfig.get('overridesSnooze', False),
                                                                adminOnly=sensorUserConfig.get('adminOnly', False)))
        # Vars for "no data" warning
        self.noDataAlarmIntervalSeconds = 600
        self.noDataAlarmHasBeenSent = False
        self.lastNoNewSensorDataAvailableAlarmSentTimestamp = -1
        self.alarms = []
        self.alarmsSnoozeOverride = []
        self.alarmsAdminOnly = []
        self.alarmsAdminOnlySnoozeOverride = []
        self.lastEntryID = None
        self.channelName = None

    def getSensorAPIResponse(self) -> dict:
        # https://community.thingspeak.com/documentation%20.../api/
        conn = HTTP20Connection('api.thingspeak.com')
        conn.request("GET", '/channels/' + str(self.cfg[Config.THINGSPEAK_CHANNEL]) + '/feed.json?key=' + self.cfg[Config.THINGSPEAK_READ_APIKEY] + '&offset=1')
        apiResult = loads(conn.get_response().read())
        return apiResult

    def setAlarmIntervalNoData(self, seconds: int):
        """ Return alarms if no new sensor data is available every X minutes.
        Set this to -1 to disable alarms on no data. """
        self.noDataAlarmIntervalSeconds = seconds * 60

    def setAlarmIntervalSensors(self, seconds: int):
        self.sensorAlarmIntervalSeconds = seconds * 60

    def getAlarmText(self) -> Union[str, None]:
        if len(self.alarms) == 0:
            return None
        else:
            text = ""
            index = 0
            for alarmMsg in self.alarms:
                if index > 0:
                    text += "\n"
                text += alarmMsg
            return text

    def getAlarmTextSnoozeOverride(self) -> Union[str, None]:
        if len(self.alarmsSnoozeOverride) == 0:
            return None
        else:
            text = ""
            index = 0
            for alarmMsg in self.alarmsSnoozeOverride:
                if index > 0:
                    text += "\n"
                text += alarmMsg
            return text

    def getAlarmTextAdminOnly(self) -> Union[str, None]:
        if len(self.alarmsAdminOnly) == 0:
            return None
        else:
            text = "<b>Admin Alarm! " + self.channelName + "</b>"
            index = 0
            for alarmMsg in self.alarmsAdminOnly:
                if index > 0:
                    text += "\n"
                text += alarmMsg
            return text

    def getAlarmTextAdminOnlySnoozeOverride(self) -> Union[str, None]:
        if len(self.alarmsAdminOnlySnoozeOverride) == 0:
            return None
        else:
            text = "<b>Admin Alarm! " + self.channelName + "</b>"
            index = 0
            for alarmMsg in self.alarmsAdminOnlySnoozeOverride:
                if index > 0:
                    text += "\n"
                text += alarmMsg
            return text

    def updateAlarms(self):
        """ Updates sensor states and saves/sets resulting alarms """
        # Clear last list of alarms
        self.alarms = []
        self.alarmsSnoozeOverride = []
        self.alarmsAdminOnly = []
        apiResult = self.getSensorAPIResponse()
        channelInfo = apiResult['channel']
        self.channelName = channelInfo["name"]
        sensorResults = apiResult['feeds']
        # Most of all times we want to check only new entries but if e.g. the channel gets reset we need to check entries lower than our last saved number!
        checkOnlyHigherEntryIDs = True
        currentLastEntryID = channelInfo['last_entry_id']
        if self.lastEntryID is None:
            # First run -> Make sure we don't return alarms immediately!
            self.lastEntryID = currentLastEntryID
        elif currentLastEntryID == self.lastEntryID:
            logging.info(" --> No new data available --> Last data is from: " + formatDatetimeToGermanDate(
                self.lastSensorUpdateServersideDatetime) + " -> FieldID [" + str(self.lastEntryID) + "]")
            # Check if our alarm system maybe hasn't been responding for a long amount of time. Only send alarm for this once until data is back!
            if -1 < self.noDataAlarmIntervalSeconds <= datetime.now().timestamp() - self.lastEntryIDChangeTimestamp:
                lastSensorDataIsFromDate = formatDatetimeToGermanDate(self.lastSensorUpdateServersideDatetime)
                logging.warning("Got no new sensor data for a long time! Last data is from: " + lastSensorDataIsFromDate)
                if not self.noDataAlarmHasBeenSent:
                    self.alarmsAdminOnly.append(SYMBOLS.DENY + "<b>Fehler Alarmanlage!Keine neuen Daten verfügbar!\nLetzte Sensordaten vom: " + lastSensorDataIsFromDate + "</b>")
                    self.lastNoNewSensorDataAvailableAlarmSentTimestamp = datetime.now().timestamp()
                    self.noDataAlarmHasBeenSent = True
            else:
                self.noDataAlarmHasBeenSent = False
            return
        elif currentLastEntryID < self.lastEntryID:
            # Rare case
            checkOnlyHigherEntryIDs = False
            logging.info("Thingspeak channel has been reset(?) -> Checking ALL entryIDs")
        else:
            logging.info("Checking all entryIDs > " + str(self.lastEntryID))
            pass
        alarmDatetime = None
        triggeredSensors = []
        alarmSensorsNames = []
        for feed in sensorResults:
            # Check all fields for which we got alarm state mapping
            entryID = feed['entry_id']
            for fieldID, sensor in self.sensors.items():
                fieldKey = 'field' + str(fieldID)
                if fieldKey not in feed:
                    logging.warning("One of your configured sensors is not available in feed: " + fieldKey + " | " + sensor.getName())
                    continue
                sensorWasTriggeredBefore = sensor.isTriggered()
                # Thingspeak sends all values as String but we need float or int
                fieldValueRaw = feed[fieldKey]
                if '.' in fieldValueRaw:
                    sensor.setValue(float(fieldValueRaw))
                else:
                    sensor.setValue(int(fieldValueRaw))
                if entryID <= self.lastEntryID and checkOnlyHigherEntryIDs:
                    # Ignore possible alarms of entries we've checked before --> We actually do this inside this loop to be able to full all sensor values right on the first start
                    continue
                thisDatetime = datetime.strptime(feed['created_at'], '%Y-%m-%dT%H:%M:%S%z')
                self.lastSensorUpdateServersideDatetime = thisDatetime
                # Check if alarm state is given
                if sensor.isTriggered():
                    if sensor.isAlarmOnlyOnceUntilUntriggered() and sensorWasTriggeredBefore:
                        # print("Ignore " + sensor.getName() + " because alarm is only allowed once until untriggered")
                        logging.info("Ignore " + sensor.getName() + " because alarm is only allowed once when triggered until untriggered")
                        continue
                    if sensor.getName() not in alarmSensorsNames:
                        alarmSensorsNames.append(sensor.getName())
                        triggeredSensors.append(sensor)
                        alarmDatetime = thisDatetime

        if len(alarmSensorsNames) > 0:
            print("Alarms triggered: " + formatDatetimeToGermanDate(alarmDatetime) + " | " + ', '.join(alarmSensorsNames))
            if datetime.now().timestamp() < (self.lastSensorAlarmSentTimestamp + self.sensorAlarmIntervalSeconds):
                # Only allow alarms every X minutes otherwise we'd send new messages every time this code gets executed!
                logging.info("Not setting alarms because: Flood protection")
            else:
                logging.warning("Setting alarms...")
                for triggeredSensor in triggeredSensors:
                    # TODO: Make use of Sensor.getAlarmText()
                    alarmText = formatDatetimeToGermanDate(alarmDatetime) + ' | ' + triggeredSensor.getName()
                    if triggeredSensor.isAdminOnlyAlarm and triggeredSensor.overridesSnooze:
                        self.alarmsAdminOnlySnoozeOverride.append(alarmText)
                    elif triggeredSensor.isAdminOnlyAlarm:
                        self.alarmsAdminOnly.append(alarmText)
                    elif triggeredSensor.overridesSnooze:
                        self.alarmsSnoozeOverride.append(alarmText)
                    else:
                        self.alarms.append(alarmText)
                    # Store these separately
                    if triggeredSensor.overridesSnooze:
                        self.alarmsSnoozeOverride.append(alarmText)
                self.lastSensorAlarmSentTimestamp = datetime.now().timestamp()

        self.lastEntryID = currentLastEntryID
        self.lastEntryIDChangeTimestamp = datetime.now().timestamp()
