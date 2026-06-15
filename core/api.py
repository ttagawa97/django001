import logging
import json
import secrets
import time
from base64 import b64decode
from binascii import Error as Base64Error

from django.contrib.auth.hashers import check_password, make_password
from django.db.models import Count, Q
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, MethodNotAllowed, NotFound, ValidationError
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .data_ingestion import parse_csv_payload, parse_json_payload, store_parsed_values
from .models import (
    ApiToken,
    AuditLog,
    Company,
    Device,
    DeviceStatus,
    PlatformUser,
    ParsedData,
    RawData,
    Site,
    Status,
    Threshold,
    UserRole,
)
from .serializers import (
    AuditLogSerializer,
    CompanySerializer,
    DashboardSummarySerializer,
    DeviceColumnSerializer,
    DeviceLatestSerializer,
    DeviceSerializer,
    LoginSerializer,
    PasswordResetExecuteSerializer,
    PlatformUserSerializer,
    SiteSerializer,
    ThresholdSerializer,
)


logger = logging.getLogger(__name__)

WRITE_ROLES = {
    UserRole.SYSTEM_ADMIN,
    UserRole.COMPANY_ADMIN,
    UserRole.SITE_ADMIN,
}


class DeviceAuthenticationFailed(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = 'Invalid device credentials.'
    default_code = 'device_authentication_failed'


def scoped_queryset(queryset, user):
    model = queryset.model
    if user.role == UserRole.SYSTEM_ADMIN:
        return queryset

    if model is Company:
        return queryset.filter(pk=user.company_id)

    if model is Site:
        if user.role == UserRole.COMPANY_ADMIN:
            return queryset.filter(company=user.company)
        return queryset.filter(pk=user.site_id)

    if user.role == UserRole.COMPANY_ADMIN:
        return queryset.filter(company=user.company)
    return queryset.filter(company=user.company, site=user.site)


def audit(request, action_name, target):
    user = getattr(request, 'poc_user', None)
    company = getattr(target, 'company', None)
    site = getattr(target, 'site', None)
    AuditLog.objects.create(
        user=user,
        company=company,
        site=site,
        action=action_name,
        target_type=target.__class__.__name__,
        target_id=str(target.pk),
        after_value={'id': target.pk},
    )


def _request_user_context(request):
    user = getattr(request, 'poc_user', None)
    if user is None:
        return {'user_id': None, 'role': 'anonymous'}
    return {'user_id': user.pk, 'role': user.role}


class ApiLoggingMixin:
    def _log_context(self, request):
        request = getattr(self, 'request', request)
        context = _request_user_context(request)
        django_request = getattr(request, '_request', request)
        context.update(
            {
                'method': request.method,
                'path': django_request.get_full_path(),
                'view': self.__class__.__name__,
                'action': getattr(self, 'action', None),
            }
        )
        return context

    def dispatch(self, request, *args, **kwargs):
        start_time = time.monotonic()
        start_context = self._log_context(request)
        logger.info(
            'api_request_started method=%s path=%s view=%s action=%s user_id=%s role=%s',
            start_context['method'],
            start_context['path'],
            start_context['view'],
            start_context['action'],
            start_context['user_id'],
            start_context['role'],
        )

        try:
            response = super().dispatch(request, *args, **kwargs)
        except Exception:
            error_context = self._log_context(request)
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(
                'api_request_failed method=%s path=%s view=%s action=%s user_id=%s role=%s duration_ms=%s',
                error_context['method'],
                error_context['path'],
                error_context['view'],
                error_context['action'],
                error_context['user_id'],
                error_context['role'],
                duration_ms,
            )
            raise

        end_context = self._log_context(request)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            'api_request_finished method=%s path=%s view=%s action=%s user_id=%s role=%s status_code=%s duration_ms=%s',
            end_context['method'],
            end_context['path'],
            end_context['view'],
            end_context['action'],
            end_context['user_id'],
            end_context['role'],
            response.status_code,
            duration_ms,
        )
        return response


class LoggedAPIView(ApiLoggingMixin, APIView):
    pass


class LoggedModelViewSet(ApiLoggingMixin, viewsets.ModelViewSet):
    pass


class LoggedGenericViewSet(ApiLoggingMixin, viewsets.GenericViewSet):
    pass


class LoginView(LoggedAPIView):
    allow_anonymous = True
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = PlatformUser.objects.select_related('company', 'site').filter(
            login_id=serializer.validated_data['login_id'],
            status=Status.ACTIVE,
        ).first()
        if user is None or not check_password(serializer.validated_data['password'], user.password_hash):
            return Response(
                {
                    'success': False,
                    'error': {
                        'code': 'invalid_credentials',
                        'message': 'Login ID or password is incorrect.',
                    },
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        token = ApiToken.objects.create(user=user, key=secrets.token_hex(32))
        return Response(
            {
                'token': token.key,
                'user': PlatformUserSerializer(user).data,
            }
        )


class LogoutView(LoggedAPIView):
    def post(self, request):
        if request.auth:
            request.auth.delete()
        return Response({'logged_out': True})


class PasswordResetRequestView(LoggedAPIView):
    allow_anonymous = True
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        return Response({'message': 'Password reset mail is not implemented in PoC.'})


class PasswordResetExecuteView(LoggedAPIView):
    allow_anonymous = True
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = PasswordResetExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = PlatformUser.objects.filter(
            login_id=serializer.validated_data['login_id'],
            status=Status.ACTIVE,
        ).first()
        if user is None:
            raise NotFound('Active user was not found.')
        user.password_hash = make_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password_hash', 'updated_at'])
        return Response({'password_reset': True})


class DeviceDataReceiveView(LoggedAPIView):
    allow_anonymous = True
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        device = self._authenticate_device(request)
        content_type = request.content_type.split(';', 1)[0].lower()
        body = request.body
        raw_payload = self._raw_payload(body, content_type)
        raw_data = RawData.objects.create(
            company=device.company,
            site=device.site,
            device=device,
            content_type=content_type,
            payload=raw_payload,
        )

        try:
            expected_content_type = {
                'json': 'application/json',
                'csv': 'text/csv',
            }[device.input_type]
            if content_type != expected_content_type:
                raise ValidationError(
                    f'Content-Type must be {expected_content_type} for this device.'
                )
            if content_type == 'application/json':
                payload = json.loads(body.decode('utf-8-sig'))
                device_timestamp, values = parse_json_payload(payload, device)
            elif content_type == 'text/csv':
                device_timestamp, values = parse_csv_payload(body.decode('utf-8-sig'), device)
            else:
                raise ValidationError('Content-Type must be application/json or text/csv.')
            stored_count, received_at = store_parsed_values(
                device, raw_data, device_timestamp, values
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            raw_data.is_error = True
            raw_data.error_message = str(exc.detail if isinstance(exc, ValidationError) else exc)
            raw_data.save(update_fields=['is_error', 'error_message'])
            return Response(
                {
                    'accepted': True,
                    'raw_data_id': raw_data.pk,
                    'processing_status': 'error_recorded',
                    'processing_error': raw_data.error_message,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return Response(
            {
                'accepted': True,
                'raw_data_id': raw_data.pk,
                'device_id': device.device_id,
                'stored_values': stored_count,
                'received_at': received_at,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _authenticate_device(self, request):
        authorization = request.headers.get('Authorization', '')
        scheme, _, encoded = authorization.partition(' ')
        if scheme.lower() != 'basic' or not encoded:
            self._authentication_failed()
        try:
            auth_id, password = b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
        except (Base64Error, UnicodeDecodeError, ValueError):
            self._authentication_failed()

        devices = Device.objects.select_related('company', 'site').prefetch_related('columns').filter(
            auth_id=auth_id,
            status=Status.ACTIVE,
        )
        device = next(
            (candidate for candidate in devices if check_password(password, candidate.auth_password_hash)),
            None,
        )
        if device is None:
            self._authentication_failed()
        return device

    def _authentication_failed(self):
        raise DeviceAuthenticationFailed()

    def _raw_payload(self, body, content_type):
        if content_type == 'application/json':
            try:
                return json.loads(body.decode('utf-8-sig'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {'raw': body.decode('utf-8', errors='replace')}
        try:
            return {'raw': body.decode('utf-8-sig')}
        except UnicodeDecodeError:
            return {'raw': body.decode('utf-8', errors='replace')}


class CompanyViewSet(LoggedModelViewSet):
    serializer_class = CompanySerializer
    queryset = Company.objects.all().order_by('id')
    allowed_roles = [UserRole.SYSTEM_ADMIN]

    def retrieve(self, request, *args, **kwargs):
        raise MethodNotAllowed('GET')

    def partial_update(self, request, *args, **kwargs):
        raise MethodNotAllowed('PATCH')

    def destroy(self, request, *args, **kwargs):
        raise MethodNotAllowed('DELETE')

    @action(detail=True, methods=['post'])
    def disable(self, request, pk=None):
        company = self.get_object()
        company.status = Status.INACTIVE
        company.save(update_fields=['status', 'updated_at'])
        audit(request, 'disable_company', company)
        return Response(self.get_serializer(company).data)

    def perform_create(self, serializer):
        company = serializer.save()
        audit(self.request, 'create_company', company)

    def perform_update(self, serializer):
        company = serializer.save()
        audit(self.request, 'update_company', company)


class ScopedModelViewSet(LoggedModelViewSet):
    allowed_roles = [
        UserRole.SYSTEM_ADMIN,
        UserRole.COMPANY_ADMIN,
        UserRole.SITE_ADMIN,
        UserRole.GENERAL_USER,
    ]
    write_roles = WRITE_ROLES
    query_filter_map = {}

    def get_queryset(self):
        return scoped_queryset(super().get_queryset(), self.request.poc_user)

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        for query_name, field_name in self.query_filter_map.items():
            value = self.request.query_params.get(query_name)
            if value not in (None, ''):
                queryset = queryset.filter(**{field_name: value})
        return queryset

    def check_write_permission(self):
        if self.request.poc_user.role not in self.write_roles:
            self.permission_denied(self.request, message='Write permission is not allowed for this role.')

    def create(self, request, *args, **kwargs):
        self.check_write_permission()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self.check_write_permission()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        raise MethodNotAllowed('PATCH')

    def destroy(self, request, *args, **kwargs):
        raise MethodNotAllowed('DELETE')

    def retrieve(self, request, *args, **kwargs):
        raise MethodNotAllowed('GET')


class SiteViewSet(ScopedModelViewSet):
    serializer_class = SiteSerializer
    queryset = Site.objects.select_related('company').all().order_by('id')
    allowed_roles = [
        UserRole.SYSTEM_ADMIN,
        UserRole.COMPANY_ADMIN,
        UserRole.SITE_ADMIN,
        UserRole.GENERAL_USER,
    ]
    write_roles = {UserRole.SYSTEM_ADMIN, UserRole.COMPANY_ADMIN}
    query_filter_map = {
        'company_id': 'company_id',
        'site_id': 'pk',
    }

    @action(detail=True, methods=['post'])
    def disable(self, request, pk=None):
        self.check_write_permission()
        site = self.get_object()
        site.status = Status.INACTIVE
        site.save(update_fields=['status', 'updated_at'])
        audit(request, 'disable_site', site)
        return Response(self.get_serializer(site).data)

    def perform_create(self, serializer):
        site = serializer.save()
        audit(self.request, 'create_site', site)

    def perform_update(self, serializer):
        site = serializer.save()
        audit(self.request, 'update_site', site)


class PlatformUserViewSet(ScopedModelViewSet):
    serializer_class = PlatformUserSerializer
    queryset = PlatformUser.objects.select_related('company', 'site').all().order_by('id')
    allowed_roles = [UserRole.SYSTEM_ADMIN, UserRole.COMPANY_ADMIN, UserRole.SITE_ADMIN]
    query_filter_map = {
        'company_id': 'company_id',
        'site_id': 'site_id',
    }

    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        self.check_write_permission()
        user = self.get_object()
        password = request.data.get('password') or secrets.token_urlsafe(12)
        user.password_hash = make_password(password)
        user.save(update_fields=['password_hash', 'updated_at'])
        audit(request, 'reset_user_password', user)
        return Response({'temporary_password': password})

    def perform_create(self, serializer):
        user = serializer.save()
        audit(self.request, 'create_user', user)

    def perform_update(self, serializer):
        user = serializer.save()
        audit(self.request, 'update_user', user)


class DeviceViewSet(ScopedModelViewSet):
    serializer_class = DeviceSerializer
    lookup_field = 'device_id'
    queryset = Device.objects.select_related(
        'company',
        'site',
        'communication_status',
    ).prefetch_related(
        'columns',
        'latest_values',
        'thresholds',
    ).all().order_by('id')
    query_filter_map = {
        'company_id': 'company_id',
        'site_id': 'site_id',
    }

    def retrieve(self, request, *args, **kwargs):
        return LoggedModelViewSet.retrieve(self, request, *args, **kwargs)

    @action(detail=False, methods=['get'])
    def latest(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        return Response(DeviceLatestSerializer(queryset, many=True).data)

    @action(detail=True, methods=['get'])
    def columns(self, request, device_id=None):
        device = self.get_object()
        return Response(DeviceColumnSerializer(device.columns.all(), many=True).data)

    @action(detail=True, methods=['get'])
    def graph(self, request, device_id=None):
        device = self.get_object()
        points = ParsedData.objects.filter(device=device, is_valid=True).order_by('device_timestamp', 'id')

        from_value = request.query_params.get('from')
        to_value = request.query_params.get('to')
        column_name = request.query_params.get('column_name')

        if from_value:
            from_datetime = parse_datetime(from_value)
            if from_datetime is None:
                raise ValidationError({'from': 'from must be an ISO 8601 datetime.'})
            points = points.filter(device_timestamp__gte=from_datetime)
        if to_value:
            to_datetime = parse_datetime(to_value)
            if to_datetime is None:
                raise ValidationError({'to': 'to must be an ISO 8601 datetime.'})
            points = points.filter(device_timestamp__lte=to_datetime)
        if column_name:
            points = points.filter(column_name=column_name)

        return Response(
            {
                'device_id': device.device_id,
                'points': list(points.values(
                    'column_name',
                    'raw_value',
                    'display_value',
                    'device_timestamp',
                    'server_timestamp',
                    'is_valid',
                )),
            }
        )

    @action(detail=True, methods=['post'])
    def disable(self, request, device_id=None):
        self.check_write_permission()
        device = self.get_object()
        device.status = Status.INACTIVE
        device.save(update_fields=['status', 'updated_at'])
        audit(request, 'disable_device', device)
        return Response(self.get_serializer(device).data)

    def perform_create(self, serializer):
        device = serializer.save()
        audit(self.request, 'create_device', device)

    def perform_update(self, serializer):
        device = serializer.save()
        audit(self.request, 'update_device', device)


class ThresholdViewSet(ScopedModelViewSet):
    serializer_class = ThresholdSerializer
    queryset = Threshold.objects.select_related('company', 'site', 'device').all().order_by('id')
    allowed_roles = [
        UserRole.SYSTEM_ADMIN,
        UserRole.COMPANY_ADMIN,
        UserRole.SITE_ADMIN,
        UserRole.GENERAL_USER,
    ]
    query_filter_map = {
        'company_id': 'company_id',
        'site_id': 'site_id',
        'device_id': 'device__device_id',
        'column_name': 'column_name',
    }

    def destroy(self, request, *args, **kwargs):
        self.check_write_permission()
        return LoggedModelViewSet.destroy(self, request, *args, **kwargs)

    def perform_create(self, serializer):
        threshold = serializer.save()
        audit(self.request, 'create_threshold', threshold)

    def perform_update(self, serializer):
        threshold = serializer.save()
        audit(self.request, 'update_threshold', threshold)

    def perform_destroy(self, instance):
        audit(self.request, 'delete_threshold', instance)
        instance.delete()


class AuditLogViewSet(mixins.ListModelMixin, LoggedGenericViewSet):
    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.select_related('user', 'company', 'site').all().order_by('-created_at')
    allowed_roles = [UserRole.SYSTEM_ADMIN, UserRole.COMPANY_ADMIN, UserRole.SITE_ADMIN]

    def get_queryset(self):
        user = self.request.poc_user
        queryset = super().get_queryset()
        if user.role == UserRole.COMPANY_ADMIN:
            queryset = queryset.filter(company=user.company)
        elif user.role != UserRole.SYSTEM_ADMIN:
            queryset = queryset.filter(company=user.company, site=user.site)

        company_id = self.request.query_params.get('company_id')
        site_id = self.request.query_params.get('site_id')
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        if site_id:
            queryset = queryset.filter(site_id=site_id)
        return queryset


class DashboardSummaryView(LoggedAPIView):
    def get(self, request):
        devices = scoped_queryset(Device.objects.all(), request.poc_user)
        sites = scoped_queryset(Site.objects.all(), request.poc_user)
        status_counts = DeviceStatus.objects.filter(device__in=devices).aggregate(
            normal=Count('id', filter=Q(status='normal')),
            disconnected=Count('id', filter=Q(status='disconnected')),
        )
        alert_devices = devices.exclude(latest_values__threshold_status='normal').filter(
            latest_values__isnull=False
        ).distinct().count()
        data = {
            'total_sites': sites.count(),
            'total_devices': devices.count(),
            'normal_devices': status_counts['normal'] or 0,
            'disconnected_devices': status_counts['disconnected'] or 0,
            'threshold_alert_devices': alert_devices,
        }
        return Response(DashboardSummarySerializer(data).data)


class MasterCompaniesView(LoggedAPIView):
    def get(self, request):
        queryset = scoped_queryset(Company.objects.all(), request.poc_user).filter(status=Status.ACTIVE).order_by('name')
        return Response(CompanySerializer(queryset, many=True).data)


class MasterSitesView(LoggedAPIView):
    def get(self, request):
        queryset = scoped_queryset(Site.objects.select_related('company'), request.poc_user).filter(status=Status.ACTIVE).order_by('site_name')
        return Response(SiteSerializer(queryset, many=True).data)


class MasterDevicesView(LoggedAPIView):
    def get(self, request):
        queryset = scoped_queryset(Device.objects.select_related('company', 'site').prefetch_related('columns'), request.poc_user).filter(status=Status.ACTIVE).order_by('device_name')
        return Response(DeviceSerializer(queryset, many=True).data)
