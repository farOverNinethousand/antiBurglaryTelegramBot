from datetime import datetime
from typing import Union


class Sensor:

    def __init__(self, name: str, triggerValue: Union[int, float], triggerOperator: str, alarmOnlyOnceUntilUntriggered: bool = False):

        self.name = name
        self.triggerValue = triggerValue
        self.triggerOperator = triggerOperator
        self.alarmOnceOnceUntilUntriggered = alarmOnlyOnceUntilUntriggered
        self.value = None
        self.lastTimeTriggered = -1

    def getName(self):
        return self.name

    def getTriggeredText(self):
        """ E.g. "Door open" """
        return None

    def getNotTriggeredText(self):
        """ E.g. "Door closed" """
        return None

    def getStatusText(self):
        if self.isTriggered():
            return self.getTriggeredText()
        else:
            return self.getNotTriggeredText()

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

    def getValue(self):
        return self.value


if __name__ == '__main__':
    sensor1 = Sensor("Batterie", 11.4, "LESS", False)
    sensor1.setValue(11.4)

    sensorNew = Sensor("Batterie2", 11.4, "LESS", False)
    sensor1.setValue(11.4)