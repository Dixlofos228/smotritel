# ошибки без ключей и содержимого сообщений
import logging
import re
import traceback


class SafeFormatter(logging.Formatter):
    def __init__(self, secrets=()):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.secrets = tuple(s for s in secrets if s)

    def redact(self, text):
        for value in self.secrets:
            text = text.replace(value, "[REDACTED]")
        return re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED]", text)

    def formatException(self, info):

        frames = traceback.extract_tb(info[2])
        stack = "".join(f'  File "{f.filename}", line {f.lineno}, in {f.name}\n' for f in frames)
        return self.redact("Traceback (most recent call last):\n" + stack + info[0].__name__)

    def format(self, record):
        return self.redact(super().format(record))
