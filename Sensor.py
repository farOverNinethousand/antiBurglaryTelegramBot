class Sensor:

    def __init__(self, name: str, triggerValue, triggerOperator: str, alarmOnlyOnceUntilUntriggered: bool = False):

        self.name = name
        self.triggerValue = triggerValue
        self.triggerOperator = triggerOperator
        self.alarmOnceOnceUntilUntriggered = alarmOnlyOnceUntilUntriggered
        self.value = None

    def getName(self):
        return self.name

    def isTriggered(self) -> bool:
        # TODO: Add support for more operators
        if self.triggerOperator == 'LESS':
            return self.value < self.triggerValue
        elif self.triggerOperator == 'MORE':
            return self.value > self.triggerValue
        else:
            # Assume it's "EQ" -> operator 'equals'
            return self.value == self.triggerValue

    def isAlarmOnlyOnceUntilUntriggered(self) -> bool:
        return self.alarmOnceOnceUntilUntriggered

    def setValue(self, value):
        self.value = value

    def getValue(self):
        return self.value


if __name__ == '__main__':
    sensor1 = Sensor("Batterie", 11.4, "LESS", False)
    sensor1.setValue(11.4)

    sensorNew = Sensor("Batterie2", 11.4, "LESS", False)
    sensor1.setValue(11.4)