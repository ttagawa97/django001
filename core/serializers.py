from django.contrib.auth.hashers import make_password
from rest_framework import serializers

from .models import (
    AuditLog,
    Company,
    Device,
    DeviceColumn,
    DeviceStatus,
    LatestValue,
    PlatformUser,
    Site,
    Status,
    Threshold,
    UserRole,
)


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['id', 'name', 'status', 'created_at', 'updated_at']


class SiteSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)

    class Meta:
        model = Site
        fields = ['id', 'company', 'company_name', 'site_name', 'address', 'status', 'created_at', 'updated_at']


class PlatformUserSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    site_name = serializers.CharField(source='site.site_name', read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = PlatformUser
        fields = [
            'id',
            'company',
            'company_name',
            'site',
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


class LatestValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = LatestValue
        fields = ['column_name', 'raw_value', 'display_value', 'device_timestamp', 'server_timestamp', 'threshold_status']


class DeviceLatestSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name')
    site_name = serializers.CharField(source='site.site_name')
    latest_values = LatestValueSerializer(many=True)
    latest_received_at = serializers.DateTimeField(source='communication_status.last_received_at', allow_null=True)
    communication_status = serializers.CharField(source='communication_status.status', default='unknown')
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
            'communication_status',
            'alert_status',
        ]

    def get_alert_status(self, obj):
        if any(value.threshold_status != 'normal' for value in obj.latest_values.all()):
            return 'alert'
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
        if attrs.get('lower_limit') is None and attrs.get('upper_limit') is None:
            raise serializers.ValidationError('lower_limit or upper_limit is required.')
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    changed_at = serializers.DateTimeField(source='created_at')
    changed_by = serializers.CharField(source='user.login_id', default='')
    company_name = serializers.CharField(source='company.name', default='')
    site_name = serializers.CharField(source='site.site_name', default='')

    class Meta:
        model = AuditLog
        fields = [
            'id',
            'changed_at',
            'changed_by',
            'company_name',
            'site_name',
            'action',
            'target_type',
            'target_id',
            'before_value',
            'after_value',
        ]


class LoginSerializer(serializers.Serializer):
    login_id = serializers.CharField()
    password = serializers.CharField(write_only=True)


class DashboardSummarySerializer(serializers.Serializer):
    total_sites = serializers.IntegerField()
    total_devices = serializers.IntegerField()
    normal_devices = serializers.IntegerField()
    disconnected_devices = serializers.IntegerField()
    threshold_alert_devices = serializers.IntegerField()
