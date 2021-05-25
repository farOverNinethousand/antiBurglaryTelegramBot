from datetime import datetime

from pydantic import BaseModel
from typing import Optional, Union


class SensorConfig(BaseModel):
    name: str
    triggerValue: Union[int, float]
    triggerOperator: str
    alarmOnlyOnceUntilUntriggered: Optional[bool] = False
    overridesSnooze: Optional[bool] = False
    triggeredText: Optional[str] = None
    unTriggeredText: Optional[str] = None
    adminOnly: Optional[bool] = False


class Sensor:

    def __init__(self, cfg: SensorConfig):

        self.name = cfg.name
        self.triggerValue = cfg.triggerValue
        self.triggerOperator = cfg.triggerOperator
        self.alarmOnceOnceUntilUntriggered = cfg.alarmOnlyOnceUntilUntriggered
        self.overridesSnooze = cfg.overridesSnooze
        self.value = None
        self.lastTimeTriggered = -1
        self.triggeredText = cfg.triggeredText
        self.unTriggeredText = cfg.unTriggeredText
        self.isAdminOnlyAlarm = False

    def getName(self):
        return self.name

    # def getTriggeredText(self):
    #     """ E.g. "Door open" """
    #     return None
    #
    # def getNotTriggeredText(self):
    #     """ E.g. "Door closed" """
    #     return None

    def getStatusText(self):
        if self.value is None:
            return "Undefiniert"
        if self.isTriggered():
            return self.triggeredText
        else:
            return self.unTriggeredText

    def isTriggered(self) -> bool:
        # TODO: Add support for more operators
        if self.value is None:  # No value set yet -> Not triggered
            return False
        if self.triggerOperator == 'LESS':
            return self.value < self.triggerValue
        elif self.triggerOperator == 'MORE':
            return self.value > self.triggerValue
        else:
            # Assume it's "EQ" -> operator 'equals'
            return self.value == self.triggerValue

    def isAlarmOnlyOnceUntilUntriggered(self) -> bool:
        return self.alarmOnceOnceUntilUntriggered

    def setName(self, sensorName: str):
        """ Set name of this sensor. """
        self.name = sensorName

    def setValue(self, value):
        self.value = value
        if self.isTriggered():
            self.lastTimeTriggered = datetime.now().timestamp()

    def setAdminOnlyAlarm(self, adminOnlyAlarm: bool):
        self.isAdminOnlyAlarm = adminOnlyAlarm

    def getValue(self):
        return self.value

    def getAlarmText(self) -> str:
        """ Returns text to reflect alarm e.g. "Door | Open" """
        return self.getName() + " | " + self.getStatusText()


if __name__ == '__main__':
    sensor1 = Sensor(SensorConfig(name="Batterie", triggerValue=11.4,
                                  triggerOperator="LESS",
                                  alarmOnlyOnceUntilUntriggered=False))
    sensor1.setValue(11.4)

    sensorNew = Sensor(SensorConfig(name="Batterie2", triggerValue=11.4,
                                    triggerOperator="LESS",
                                    alarmOnlyOnceUntilUntriggered=False))
    sensor1.setValue(11.4)
