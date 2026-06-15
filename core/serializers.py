from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.hashers import make_password
from django.utils import timezone
from rest_framework import serializers

from .models import (
    AuditLog,
    Company,
    Device,
    DeviceColumn,
    DeviceStatus,
    PlatformUser,
    Site,
    Status,
    Threshold,
    UserRole,
)


def _request_user(serializer):
    request = serializer.context.get('request')
    return getattr(request, 'poc_user', None)


def _validate_scope(serializer, company, site=None):
    user = _request_user(serializer)
    if user is None or user.role == UserRole.SYSTEM_ADMIN:
        return
    if company is None or company.pk != user.company_id:
        raise serializers.ValidationError({'company': 'Company is outside your permission scope.'})
    if user.role in {UserRole.SITE_ADMIN, UserRole.GENERAL_USER}:
        if site is None or site.pk != user.site_id:
            raise serializers.ValidationError({'site': 'Site is outside your permission scope.'})


def _validate_site_company(company, site):
    if company is not None and site is not None and site.company_id != company.pk:
        raise serializers.ValidationError({'site': 'Site must belong to the selected company.'})


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['id', 'name', 'status', 'created_at', 'updated_at']


class SiteSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)

    class Meta:
        model = Site
        fields = ['id', 'company', 'company_name', 'site_name', 'address', 'status', 'created_at', 'updated_at']

    def validate(self, attrs):
        company = attrs.get('company', getattr(self.instance, 'company', None))
        _validate_scope(self, company)
        return attrs


class PlatformUserSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    site_name = serializers.CharField(source='site.site_name', read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = PlatformUser
        fields = [
            'id',
            'company',
            'company_id',
            'company_name',
            'site',
            'site_id',
            'site_name',
            'role',
            'login_id',
            'user_name',
            'password',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['login_id']

    def get_extra_kwargs(self):
        kwargs = super().get_extra_kwargs()
        if self.instance is None:
            kwargs['login_id'] = {'read_only': False}
            kwargs['password'] = {'required': True, 'write_only': True}
        return kwargs

    def validate(self, attrs):
        role = attrs.get('role', getattr(self.instance, 'role', None))
        company = attrs.get('company', getattr(self.instance, 'company', None))
        site = attrs.get('site', getattr(self.instance, 'site', None))

        if role in {UserRole.COMPANY_ADMIN, UserRole.SITE_ADMIN, UserRole.GENERAL_USER} and company is None:
            raise serializers.ValidationError({'company': 'This role requires company.'})
        if role in {UserRole.SITE_ADMIN, UserRole.GENERAL_USER} and site is None:
            raise serializers.ValidationError({'site': 'This role requires site.'})
        _validate_site_company(company, site)
        _validate_scope(self, company, site)

        request_user = _request_user(self)
        if request_user is not None:
            if request_user.role == UserRole.COMPANY_ADMIN and role == UserRole.SYSTEM_ADMIN:
                raise serializers.ValidationError({'role': 'Company administrators cannot manage system administrators.'})
            if request_user.role == UserRole.SITE_ADMIN and role not in {
                UserRole.SITE_ADMIN,
                UserRole.GENERAL_USER,
            }:
                raise serializers.ValidationError({'role': 'Site administrators can manage site users only.'})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        validated_data['password_hash'] = make_password(password)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        if password:
            validated_data['password_hash'] = make_password(password)
        return super().update(instance, validated_data)


class DeviceColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceColumn
        fields = ['id', 'device', 'column_name', 'display_name', 'data_type', 'unit', 'weight', 'display_order']
        read_only_fields = ['device']


class DeviceSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    site_name = serializers.CharField(source='site.site_name', read_only=True)
    columns = DeviceColumnSerializer(many=True)
    auth_password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = Device
        fields = [
            'id',
            'company',
            'company_name',
            'site',
            'site_name',
            'device_id',
            'device_name',
            'auth_id',
            'auth_password',
            'input_type',
            'csv_header_mode',
            'status',
            'columns',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['device_id']

    def get_extra_kwargs(self):
        kwargs = super().get_extra_kwargs()
        if self.instance is None:
            kwargs['device_id'] = {'read_only': False}
            kwargs['auth_password'] = {'required': True, 'write_only': True}
        return kwargs

    def validate_columns(self, value):
        if not 1 <= len(value) <= 256:
            raise serializers.ValidationError('Device requires 1 to 256 columns.')
        names = [item['column_name'] for item in value]
        if len(names) != len(set(names)):
            raise serializers.ValidationError('column_name must be unique per device.')
        return value

    def validate(self, attrs):
        company = attrs.get('company', getattr(self.instance, 'company', None))
        site = attrs.get('site', getattr(self.instance, 'site', None))
        _validate_site_company(company, site)
        _validate_scope(self, company, site)

        input_type = attrs.get('input_type', getattr(self.instance, 'input_type', 'json'))
        csv_header_mode = attrs.get(
            'csv_header_mode',
            getattr(self.instance, 'csv_header_mode', ''),
        )
        if not csv_header_mode:
            if input_type == 'json':
                attrs['csv_header_mode'] = 'header_exists'
            else:
                raise serializers.ValidationError(
                    {'csv_header_mode': 'This field is required for CSV devices.'}
                )
        return attrs

    def create(self, validated_data):
        columns = validated_data.pop('columns')
        auth_password = validated_data.pop('auth_password')
        validated_data['auth_password_hash'] = make_password(auth_password)
        device = Device.objects.create(**validated_data)
        for column in columns:
            DeviceColumn.objects.create(device=device, **column)
        DeviceStatus.objects.create(company=device.company, site=device.site, device=device)
        return device

    def update(self, instance, validated_data):
        columns = validated_data.pop('columns', None)
        auth_password = validated_data.pop('auth_password', None)
        if auth_password:
            validated_data['auth_password_hash'] = make_password(auth_password)
        device = super().update(instance, validated_data)
        if columns is not None:
            device.columns.all().delete()
            for column in columns:
                DeviceColumn.objects.create(device=device, **column)
        return device


class DeviceLatestSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name')
    site_name = serializers.CharField(source='site.site_name')
    latest_values = serializers.SerializerMethodField()
    latest_received_at = serializers.SerializerMethodField()
    estimated_interval_seconds = serializers.SerializerMethodField()
    communication_status = serializers.SerializerMethodField()
    alert_status = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            'id',
            'company_name',
            'site_name',
            'device_name',
            'device_id',
            'latest_values',
            'latest_received_at',
            'estimated_interval_seconds',
            'communication_status',
            'alert_status',
        ]

    def _column_map(self, obj):
        return {column.column_name: column for column in obj.columns.all()}

    def _typed_value(self, value, column):
        if column is None:
            return value
        if column.data_type == 'number':
            try:
                return float(Decimal(str(value)))
            except (InvalidOperation, TypeError, ValueError):
                return value
        if column.data_type == 'boolean':
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in {'true', 'false'}:
                return value.lower() == 'true'
        return value

    def get_latest_values(self, obj):
        columns = self._column_map(obj)
        return [
            {
                'column_name': value.column_name,
                'display_name': columns[value.column_name].display_name
                if value.column_name in columns
                else value.column_name,
                'unit': columns[value.column_name].unit
                if value.column_name in columns
                else '',
                'raw_value': self._typed_value(
                    value.raw_value,
                    columns.get(value.column_name),
                ),
                'display_value': self._typed_value(
                    value.display_value,
                    columns.get(value.column_name),
                ),
            }
            for value in obj.latest_values.all()
        ]

    def get_latest_received_at(self, obj):
        device_status = getattr(obj, 'communication_status', None)
        return device_status.last_received_at if device_status is not None else None

    def get_estimated_interval_seconds(self, obj):
        device_status = getattr(obj, 'communication_status', None)
        if device_status is None or not device_status.estimated_interval_seconds:
            return None
        return device_status.estimated_interval_seconds

    def get_communication_status(self, obj):
        device_status = getattr(obj, 'communication_status', None)
        if (
            device_status is None
            or device_status.last_received_at is None
            or not device_status.estimated_interval_seconds
        ):
            return 'unknown'
        deadline = device_status.last_received_at + timedelta(
            seconds=device_status.estimated_interval_seconds
        )
        return 'online' if timezone.now() <= deadline else 'offline'

    def get_alert_status(self, obj):
        thresholds = {}
        for threshold in obj.thresholds.all():
            thresholds.setdefault(threshold.column_name, []).append(threshold)

        columns = self._column_map(obj)
        has_comparable_value = False
        has_unconfigured_value = False
        has_configured_threshold = bool(thresholds)
        for latest in obj.latest_values.all():
            column = columns.get(latest.column_name)
            try:
                value = Decimal(str(latest.display_value or latest.raw_value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            has_comparable_value = True
            column_thresholds = thresholds.get(latest.column_name, [])
            if not column_thresholds:
                has_unconfigured_value = True
                continue
            for threshold in column_thresholds:
                if threshold.lower_limit is not None and value < threshold.lower_limit:
                    return 'alert'
                if threshold.upper_limit is not None and value > threshold.upper_limit:
                    return 'alert'

        if has_unconfigured_value or not has_configured_threshold:
            return 'unconfigured'
        if not has_comparable_value:
            return 'indeterminate'
        return 'normal'


class ThresholdSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    site_name = serializers.CharField(source='site.site_name', read_only=True)
    device_name = serializers.CharField(source='device.device_name', read_only=True)
    column_display_name = serializers.SerializerMethodField()
    notification_emails = serializers.CharField(source='notify_emails')

    class Meta:
        model = Threshold
        fields = [
            'id',
            'company',
            'company_name',
            'site',
            'site_name',
            'device',
            'device_name',
            'column_name',
            'column_display_name',
            'threshold_name',
            'lower_limit',
            'upper_limit',
            'notification_emails',
            'suppress_minutes',
            'current_status',
            'created_at',
            'updated_at',
        ]

    def get_column_display_name(self, obj):
        column = obj.device.columns.filter(column_name=obj.column_name).first()
        return column.display_name if column else obj.column_name

    def validate(self, attrs):
        company = attrs.get('company', getattr(self.instance, 'company', None))
        site = attrs.get('site', getattr(self.instance, 'site', None))
        device = attrs.get('device', getattr(self.instance, 'device', None))
        lower_limit = attrs.get('lower_limit', getattr(self.instance, 'lower_limit', None))
        upper_limit = attrs.get('upper_limit', getattr(self.instance, 'upper_limit', None))

        if lower_limit is None and upper_limit is None:
            raise serializers.ValidationError('lower_limit or upper_limit is required.')
        _validate_site_company(company, site)
        if device is not None and (
            device.company_id != getattr(company, 'pk', None)
            or device.site_id != getattr(site, 'pk', None)
        ):
            raise serializers.ValidationError(
                {'device': 'Device must belong to the selected company and site.'}
            )
        column_name = attrs.get(
            'column_name',
            getattr(self.instance, 'column_name', None),
        )
        if (
            device is not None
            and column_name
            and not device.columns.filter(column_name=column_name).exists()
        ):
            raise serializers.ValidationError(
                {'column_name': 'Column is not configured for the selected device.'}
            )
        _validate_scope(self, company, site)
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    changed_by = serializers.IntegerField(source='user_id', allow_null=True)
    changed_by_login_id = serializers.CharField(source='user.login_id', allow_null=True)
    company_id = serializers.IntegerField(allow_null=True)
    site_id = serializers.IntegerField(allow_null=True)
    target_type = serializers.SerializerMethodField()
    target_display_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            'id',
            'created_at',
            'changed_by',
            'changed_by_login_id',
            'action',
            'target_type',
            'target_id',
            'target_display_name',
            'company_id',
            'site_id',
            'before_value',
            'after_value',
        ]

    def get_target_display_name(self, obj):
        model_map = {
            'Company': Company,
            'Site': Site,
            'Device': Device,
            'PlatformUser': PlatformUser,
            'Threshold': Threshold,
        }
        model = model_map.get(obj.target_type)
        if model is None:
            return None
        target = model.objects.filter(pk=obj.target_id).first()
        if target is None:
            return None
        if isinstance(target, Company):
            return target.name
        if isinstance(target, Site):
            return target.site_name
        if isinstance(target, Device):
            return target.device_name
        if isinstance(target, PlatformUser):
            return target.user_name
        return target.threshold_name

    def get_target_type(self, obj):
        return {
            'Company': 'company',
            'Site': 'site',
            'Device': 'device',
            'PlatformUser': 'user',
            'Threshold': 'threshold',
        }.get(obj.target_type, 'other')


class LoginSerializer(serializers.Serializer):
    login_id = serializers.CharField()
    password = serializers.CharField(write_only=True)


class PasswordResetExecuteSerializer(serializers.Serializer):
    login_id = serializers.CharField()
    new_password = serializers.CharField(write_only=True, allow_blank=False)


class DashboardSummarySerializer(serializers.Serializer):
    total_sites = serializers.IntegerField()
    total_devices = serializers.IntegerField()
    normal_devices = serializers.IntegerField()
    disconnected_devices = serializers.IntegerField()
    threshold_alert_devices = serializers.IntegerField()
