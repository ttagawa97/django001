from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Status(models.TextChoices):
    ACTIVE = 'active', 'active'
    INACTIVE = 'inactive', 'inactive'


class UserRole(models.TextChoices):
    SYSTEM_ADMIN = 'system_admin', 'system_admin'
    COMPANY_ADMIN = 'company_admin', 'company_admin'
    SITE_ADMIN = 'site_admin', 'site_admin'
    GENERAL_USER = 'general_user', 'general_user'


class Company(TimestampedModel):
    name = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    def __str__(self):
        return self.name


class Site(TimestampedModel):
    company = models.ForeignKey(Company, related_name='sites', on_delete=models.CASCADE)
    site_name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['company', 'site_name'], name='unique_site_name_per_company'),
        ]

    def __str__(self):
        return self.site_name


class PlatformUser(TimestampedModel):
    company = models.ForeignKey(Company, null=True, blank=True, related_name='users', on_delete=models.SET_NULL)
    site = models.ForeignKey(Site, null=True, blank=True, related_name='users', on_delete=models.SET_NULL)
    role = models.CharField(max_length=30, choices=UserRole.choices)
    login_id = models.CharField(max_length=150, unique=True)
    user_name = models.CharField(max_length=255)
    password_hash = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    def __str__(self):
        return self.login_id


class ApiToken(TimestampedModel):
    user = models.ForeignKey(PlatformUser, related_name='api_tokens', on_delete=models.CASCADE)
    key = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()


class Device(TimestampedModel):
    company = models.ForeignKey(Company, related_name='devices', on_delete=models.CASCADE)
    site = models.ForeignKey(Site, related_name='devices', on_delete=models.CASCADE)
    device_id = models.CharField(max_length=150, unique=True)
    device_name = models.CharField(max_length=255)
    auth_id = models.CharField(max_length=150)
    auth_password_hash = models.CharField(max_length=255, blank=True)
    input_type = models.CharField(max_length=10, choices=[('json', 'json'), ('csv', 'csv')], default='json')
    csv_header_mode = models.CharField(
        max_length=20,
        choices=[('header_exists', 'header_exists'), ('no_header', 'no_header')],
        blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    def __str__(self):
        return self.device_id


class DeviceColumn(TimestampedModel):
    device = models.ForeignKey(Device, related_name='columns', on_delete=models.CASCADE)
    column_name = models.CharField(max_length=150)
    display_name = models.CharField(max_length=255)
    data_type = models.CharField(
        max_length=20,
        choices=[('number', 'number'), ('string', 'string'), ('boolean', 'boolean')],
    )
    unit = models.CharField(max_length=50, blank=True)
    weight = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    display_order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['device', 'column_name'], name='unique_column_name_per_device'),
        ]

    def __str__(self):
        return f'{self.device.device_id}.{self.column_name}'


class RawData(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, related_name='raw_data', on_delete=models.CASCADE)
    received_at = models.DateTimeField(default=timezone.now)
    content_type = models.CharField(max_length=100)
    payload = models.JSONField()
    is_error = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)


class ParsedData(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, related_name='parsed_data', on_delete=models.CASCADE)
    raw_data = models.ForeignKey(RawData, related_name='parsed_values', on_delete=models.CASCADE)
    device_timestamp = models.DateTimeField()
    server_timestamp = models.DateTimeField(default=timezone.now)
    column_name = models.CharField(max_length=150)
    raw_value = models.JSONField()
    display_value = models.JSONField()
    is_valid = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['device', 'device_timestamp']),
            models.Index(fields=['device', 'column_name', 'device_timestamp']),
        ]


class LatestValue(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, related_name='latest_values', on_delete=models.CASCADE)
    column_name = models.CharField(max_length=150)
    raw_value = models.CharField(max_length=255, blank=True)
    display_value = models.CharField(max_length=255, blank=True)
    device_timestamp = models.DateTimeField(null=True, blank=True)
    server_timestamp = models.DateTimeField(null=True, blank=True)
    threshold_status = models.CharField(max_length=30, default='normal')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['device', 'column_name'], name='unique_latest_value_per_column'),
        ]


class DeviceStatus(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    device = models.OneToOneField(Device, related_name='communication_status', on_delete=models.CASCADE)
    last_received_at = models.DateTimeField(null=True, blank=True)
    estimated_interval_seconds = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=30, default='unknown')


class Threshold(TimestampedModel):
    company = models.ForeignKey(Company, related_name='thresholds', on_delete=models.CASCADE)
    site = models.ForeignKey(Site, related_name='thresholds', on_delete=models.CASCADE)
    device = models.ForeignKey(Device, related_name='thresholds', on_delete=models.CASCADE)
    column_name = models.CharField(max_length=150)
    threshold_name = models.CharField(max_length=255)
    upper_limit = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    lower_limit = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    notify_emails = models.TextField()
    suppress_minutes = models.PositiveIntegerField(default=60)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    current_status = models.CharField(max_length=30, default='normal')

    def __str__(self):
        return self.threshold_name


class ThresholdEvent(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    column_name = models.CharField(max_length=150)
    threshold = models.ForeignKey(Threshold, on_delete=models.CASCADE)
    occurred_at = models.DateTimeField(default=timezone.now)
    value = models.DecimalField(max_digits=12, decimal_places=4)
    threshold_name = models.CharField(max_length=255)
    status = models.CharField(max_length=30)


class AuditLog(models.Model):
    user = models.ForeignKey(PlatformUser, null=True, blank=True, on_delete=models.SET_NULL)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100)
    before_value = models.JSONField(null=True, blank=True)
    after_value = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
