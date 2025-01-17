from decimal import Decimal
import datetime
from dimagi.utils.parsing import json_format_datetime


def serialize(value):
    """
    Serializes a value so it can properly be parsed into XML
    """
    if isinstance(value, datetime.datetime):
        return json_format_datetime(value)
    elif isinstance(value, datetime.date):
        return value.isoformat()
    elif isinstance(value, datetime.time):
        return value.strftime('%H:%M:%S')
    elif isinstance(value, (int, Decimal, float, long)):
        return unicode(value)
    else:
        return value if value is not None else ""
