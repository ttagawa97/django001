from django.contrib import admin

from .models import (
    AuditLog,
    Company,
    Device,
    DeviceColumn,
    DeviceStatus,
    LatestValue,
    PlatformUser,
    Site,
    Threshold,
    ThresholdEvent,
)

admin.site.register(AuditLog)
admin.site.register(Company)
admin.site.register(Device)
admin.site.register(DeviceColumn)
admin.site.register(DeviceStatus)
admin.site.register(LatestValue)
admin.site.register(PlatformUser)
admin.site.register(Site)
admin.site.register(Threshold)
admin.site.register(ThresholdEvent)
