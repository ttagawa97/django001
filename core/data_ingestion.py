import csv
import io
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from .models import DeviceStatus, LatestValue, ParsedData


DEVICE_CLOCK_SKEW_TOLERANCE = timedelta(minutes=15)


def parse_json_payload(payload, device):
    if not isinstance(payload, dict):
        raise ValidationError('JSON body must be an object.')
    if payload.get('device_id') != device.device_id:
        raise ValidationError({'device_id': 'device_id does not match the authenticated device.'})
    values = payload.get('values')
    if not isinstance(values, dict) or not values:
        raise ValidationError({'values': 'values must be a non-empty object.'})
    return _parse_timestamp(payload.get('timestamp')), values


def parse_csv_payload(payload, device):
    rows = list(csv.reader(io.StringIO(payload)))
    if not rows:
        raise ValidationError('CSV body is empty.')

    columns = list(device.columns.all())
    if device.csv_header_mode == 'header_exists':
        if len(rows) != 2:
            raise ValidationError('CSV with a header must contain one header row and one data row.')
        header, row = rows
        if len(header) != len(row):
            raise ValidationError('CSV header and data row have different column counts.')
        record = dict(zip(header, row))
        timestamp = _parse_timestamp(record.pop('timestamp', None))
        record.pop('device_id', None)
        return timestamp, record

    if len(rows) != 1:
        raise ValidationError('CSV without a header must contain exactly one data row.')
    row = rows[0]
    if len(row) != len(columns) + 1:
        raise ValidationError('CSV must contain timestamp followed by the configured device columns.')
    return _parse_timestamp(row[0]), {
        column.column_name: value for column, value in zip(columns, row[1:])
    }


def store_parsed_values(device, raw_data, device_timestamp, values):
    configured_columns = {column.column_name: column for column in device.columns.all()}
    unknown_columns = sorted(set(values) - set(configured_columns))
    if unknown_columns:
        raise ValidationError({'values': f'Unknown columns: {", ".join(unknown_columns)}'})

    server_timestamp = timezone.now()
    parsed_values = []
    for column_name, raw_value in values.items():
        column = configured_columns[column_name]
        display_value = _convert_value(column, raw_value)
        parsed_values.append((column_name, raw_value, display_value))

    with transaction.atomic():
        for column_name, raw_value, display_value in parsed_values:
            ParsedData.objects.create(
                company=device.company,
                site=device.site,
                device=device,
                raw_data=raw_data,
                device_timestamp=device_timestamp,
                server_timestamp=server_timestamp,
                column_name=column_name,
                raw_value=raw_value,
                display_value=display_value,
            )
            LatestValue.objects.update_or_create(
                device=device,
                column_name=column_name,
                defaults={
                    'company': device.company,
                    'site': device.site,
                    'raw_value': _display_text(raw_value),
                    'display_value': _display_text(display_value),
                    'device_timestamp': device_timestamp,
                    'server_timestamp': server_timestamp,
                    'threshold_status': 'normal',
                },
            )
        DeviceStatus.objects.update_or_create(
            device=device,
            defaults={
                'company': device.company,
                'site': device.site,
                'last_received_at': server_timestamp,
                'status': 'normal',
            },
        )
    return len(parsed_values), server_timestamp


def _parse_timestamp(value):
    if not isinstance(value, str):
        raise ValidationError({'timestamp': 'timestamp is required.'})
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise ValidationError({'timestamp': 'timestamp must be an ISO 8601 datetime with timezone.'})
    if parsed.utcoffset() != timedelta(hours=9):
        raise ValidationError({'timestamp': 'timestamp must use the Asia/Tokyo UTC+09:00 offset.'})
    if parsed > timezone.now() + DEVICE_CLOCK_SKEW_TOLERANCE:
        raise ValidationError({'timestamp': 'timestamp must not be more than 15 minutes in the future.'})
    return parsed


def _convert_value(column, raw_value):
    if column.data_type == 'number':
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({'values': {column.column_name: 'A numeric value is required.'}})
        return float(value * column.weight)
    if column.data_type == 'boolean':
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str) and raw_value.lower() in {'true', 'false'}:
            return raw_value.lower() == 'true'
        raise ValidationError({'values': {column.column_name: 'A boolean value is required.'}})
    if not isinstance(raw_value, str):
        raise ValidationError({'values': {column.column_name: 'A string value is required.'}})
    return raw_value


def _display_text(value):
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
