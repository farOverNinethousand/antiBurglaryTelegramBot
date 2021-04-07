class Sensor:

    def __init__(self, name: str, triggerValue, triggerOperator: str):

        self.name = name
        self.triggerValue = triggerValue
        self.triggerOperator = triggerOperator
        self.value = None

    def getName(self):
        return self.name

    def isTriggered(self) -> bool:
        # TODO: Add support for more operators
        if self.triggerOperator == 'LESS':
            return self.value < self.triggerValue
        else:
            return self.value == self.triggerValue

    def setValue(self, value):
        self.value = value

    def getValue(self):
        return self.value


if __name__ == '__main__':
    pass

