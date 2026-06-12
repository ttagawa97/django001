from django.urls import path
from rest_framework.routers import SimpleRouter

from .api import (
    AuditLogViewSet,
    CompanyViewSet,
    DashboardSummaryView,
    DeviceDataReceiveView,
    DeviceViewSet,
    LoginView,
    LogoutView,
    MasterCompaniesView,
    MasterDevicesView,
    MasterSitesView,
    PasswordResetExecuteView,
    PasswordResetRequestView,
    PlatformUserViewSet,
    SiteViewSet,
    ThresholdViewSet,
)

router = SimpleRouter(trailing_slash=False)
router.register('companies', CompanyViewSet, basename='company')
router.register('sites', SiteViewSet, basename='site')
router.register('users', PlatformUserViewSet, basename='user')
router.register('devices', DeviceViewSet, basename='device')
router.register('thresholds', ThresholdViewSet, basename='threshold')
router.register('audit-logs', AuditLogViewSet, basename='audit-log')

urlpatterns = [
    path('data', DeviceDataReceiveView.as_view(), name='api-device-data-receive'),
    path('auth/login', LoginView.as_view(), name='api-login'),
    path('auth/logout', LogoutView.as_view(), name='api-logout'),
    path('auth/password-reset/request', PasswordResetRequestView.as_view(), name='api-password-reset-request'),
    path('auth/password-reset/execute', PasswordResetExecuteView.as_view(), name='api-password-reset-execute'),
    path('dashboard/summary', DashboardSummaryView.as_view(), name='api-dashboard-summary'),
    path('masters/companies', MasterCompaniesView.as_view(), name='api-master-companies'),
    path('masters/sites', MasterSitesView.as_view(), name='api-master-sites'),
    path('masters/devices', MasterDevicesView.as_view(), name='api-master-devices'),
]

urlpatterns += router.urls
