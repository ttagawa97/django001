import base64
import json

from django.contrib.auth.hashers import check_password, make_password
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Company,
    ApiToken,
    AuditLog,
    Device,
    DeviceColumn,
    DeviceStatus,
    LatestValue,
    ParsedData,
    RawData,
    Site,
    PlatformUser,
    Threshold,
    UserRole,
)


class HomeViewTests(TestCase):
    def test_home_returns_ready_message(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Django development environment is ready.')


class PoCApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def login(self):
        response = self.client.post(
            '/api/v1/auth/login',
            {'login_id': 'admin', 'password': 'admin123'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        token = response.data['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_admin_can_call_management_apis(self):
        self.login()

        company_response = self.client.post(
            '/api/v1/companies',
            {'name': 'Example Company', 'status': 'active'},
            format='json',
        )
        self.assertEqual(company_response.status_code, 201)
        company_id = company_response.data['id']

        site_response = self.client.post(
            '/api/v1/sites',
            {
                'company': company_id,
                'site_name': 'Tokyo Site',
                'address': 'Tokyo',
                'status': 'active',
            },
            format='json',
        )
        self.assertEqual(site_response.status_code, 201)
        site_id = site_response.data['id']

        device_response = self.client.post(
            '/api/v1/devices',
            {
                'company': company_id,
                'site': site_id,
                'device_id': 'DEVICE001',
                'device_name': 'Temperature Sensor',
                'auth_id': 'device001',
                'auth_password': 'secret-pass',
                'input_type': 'json',
                'csv_header_mode': '',
                'status': 'active',
                'columns': [
                    {
                        'column_name': 'temp',
                        'display_name': 'Temperature',
                        'data_type': 'number',
                        'unit': 'C',
                        'weight': '1.0',
                        'display_order': 1,
                    }
                ],
            },
            format='json',
        )
        self.assertEqual(device_response.status_code, 201)

        list_response = self.client.get('/api/v1/devices/latest')
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)

    def test_api_calls_write_standard_logs(self):
        with self.assertLogs('core.api', level='INFO') as logs:
            response = self.client.post(
                '/api/v1/auth/login',
                {'login_id': 'admin', 'password': 'admin123'},
                format='json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('api_request_started method=POST path=/api/v1/auth/login' in line for line in logs.output))
        self.assertTrue(any('api_request_finished method=POST path=/api/v1/auth/login' in line for line in logs.output))
        self.assertTrue(any('status_code=200' in line for line in logs.output))
        self.assertTrue(any('role=anonymous' in line for line in logs.output))

        token = response.data['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        with self.assertLogs('core.api', level='INFO') as authenticated_logs:
            response = self.client.get('/api/v1/dashboard/summary')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('view=DashboardSummaryView' in line for line in authenticated_logs.output))
        self.assertTrue(any('role=system_admin' in line for line in authenticated_logs.output))


class DeviceDataReceiveApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        company = Company.objects.create(name='Receive Test Company')
        site = Site.objects.create(company=company, site_name='Receive Test Site')
        self.device = Device.objects.create(
            company=company,
            site=site,
            device_id='DEVICE001',
            device_name='Test Sensor',
            auth_id='device-auth',
            auth_password_hash=make_password('device-password'),
            input_type='json',
        )
        DeviceColumn.objects.create(
            device=self.device,
            column_name='temp',
            display_name='Temperature',
            data_type='number',
            unit='C',
            weight='1.5',
            display_order=1,
        )
        DeviceColumn.objects.create(
            device=self.device,
            column_name='enabled',
            display_name='Enabled',
            data_type='boolean',
            display_order=2,
        )
        DeviceStatus.objects.create(company=company, site=site, device=self.device)

    def authorization(self, password='device-password'):
        credentials = base64.b64encode(f'device-auth:{password}'.encode()).decode()
        return f'Basic {credentials}'

    def test_json_data_is_stored_in_sqlite_and_updates_latest_values(self):
        response = self.client.post(
            '/api/v1/data',
            {
                'device_id': 'DEVICE001',
                'timestamp': '2026-06-12T10:00:00+09:00',
                'values': {'temp': 20, 'enabled': True},
            },
            format='json',
            HTTP_AUTHORIZATION=self.authorization(),
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(RawData.objects.filter(device=self.device, is_error=False).count(), 1)
        self.assertEqual(ParsedData.objects.filter(device=self.device).count(), 2)
        self.assertEqual(LatestValue.objects.get(device=self.device, column_name='temp').display_value, '30.0')
        self.device.communication_status.refresh_from_db()
        self.assertEqual(self.device.communication_status.status, 'normal')
        self.assertIsNotNone(self.device.communication_status.last_received_at)

    def test_graph_api_returns_stored_parsed_data(self):
        self.client.post(
            '/api/v1/data',
            {
                'device_id': 'DEVICE001',
                'timestamp': '2026-06-12T10:00:00+09:00',
                'values': {'temp': 20, 'enabled': True},
            },
            format='json',
            HTTP_AUTHORIZATION=self.authorization(),
        )
        login_response = self.client.post(
            '/api/v1/auth/login',
            {'login_id': 'admin', 'password': 'admin123'},
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.data['token']}")

        response = self.client.get(
            f'/api/v1/devices/{self.device.device_id}/graph',
            {'column_name': 'temp'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['device_id'], 'DEVICE001')
        self.assertEqual(len(response.data['points']), 1)
        self.assertEqual(response.data['points'][0]['column_name'], 'temp')
        self.assertEqual(response.data['points'][0]['raw_value'], 20)
        self.assertEqual(response.data['points'][0]['display_value'], 30.0)

    def test_invalid_data_is_accepted_and_recorded_as_error(self):
        response = self.client.post(
            '/api/v1/data',
            {
                'device_id': 'DEVICE001',
                'timestamp': '2026-06-12T10:00:00+09:00',
                'values': {'temp': 'not-a-number'},
            },
            format='json',
            HTTP_AUTHORIZATION=self.authorization(),
        )

        self.assertEqual(response.status_code, 202)
        raw_data = RawData.objects.get(device=self.device)
        self.assertTrue(raw_data.is_error)
        self.assertIn('numeric value', raw_data.error_message)
        self.assertFalse(ParsedData.objects.exists())

    def test_invalid_credentials_are_rejected_without_storing_data(self):
        response = self.client.post(
            '/api/v1/data',
            {
                'device_id': 'DEVICE001',
                'timestamp': '2026-06-12T10:00:00+09:00',
                'values': {'temp': 20},
            },
            format='json',
            HTTP_AUTHORIZATION=self.authorization(password='wrong-password'),
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(RawData.objects.exists())

    def test_timestamp_with_non_tokyo_offset_is_recorded_as_invalid(self):
        response = self.client.post(
            '/api/v1/data',
            {
                'device_id': 'DEVICE001',
                'timestamp': '2026-06-12T10:00:00+09:20',
                'values': {'temp': 20},
            },
            format='json',
            HTTP_AUTHORIZATION=self.authorization(),
        )

        self.assertEqual(response.status_code, 202)
        raw_data = RawData.objects.get(device=self.device)
        self.assertTrue(raw_data.is_error)
        self.assertIn('UTC+09:00', raw_data.error_message)
        self.assertFalse(ParsedData.objects.exists())

    def test_timestamp_too_far_in_the_future_is_recorded_as_invalid(self):
        response = self.client.post(
            '/api/v1/data',
            {
                'device_id': 'DEVICE001',
                'timestamp': '2099-06-12T10:00:00+09:00',
                'values': {'temp': 20},
            },
            format='json',
            HTTP_AUTHORIZATION=self.authorization(),
        )

        self.assertEqual(response.status_code, 202)
        raw_data = RawData.objects.get(device=self.device)
        self.assertTrue(raw_data.is_error)
        self.assertIn('15 minutes', raw_data.error_message)
        self.assertFalse(ParsedData.objects.exists())

    def test_csv_data_with_header_is_stored(self):
        self.device.input_type = 'csv'
        self.device.csv_header_mode = 'header_exists'
        self.device.save(update_fields=['input_type', 'csv_header_mode'])
        body = 'timestamp,temp,enabled\n2026-06-12T10:00:00+09:00,10,true'

        response = self.client.generic(
            'POST',
            '/api/v1/data',
            body,
            content_type='text/csv',
            HTTP_AUTHORIZATION=self.authorization(),
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(ParsedData.objects.filter(device=self.device).count(), 2)
        self.assertEqual(LatestValue.objects.get(device=self.device, column_name='temp').display_value, '15.0')


class ApiSpecificationContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company_a = Company.objects.create(name='Contract Company A')
        self.company_b = Company.objects.create(name='Contract Company B')
        self.site_a = Site.objects.create(company=self.company_a, site_name='Contract Site A')
        self.site_b = Site.objects.create(company=self.company_b, site_name='Contract Site B')
        self.device_a = self._create_device(
            self.company_a,
            self.site_a,
            'CONTRACT-A',
        )
        self.device_b = self._create_device(
            self.company_b,
            self.site_b,
            'CONTRACT-B',
        )
        self.company_admin = PlatformUser.objects.create(
            company=self.company_a,
            role=UserRole.COMPANY_ADMIN,
            login_id='contract-company-admin',
            user_name='Company Admin',
            password_hash=make_password('password'),
        )
        self.general_user = PlatformUser.objects.create(
            company=self.company_a,
            site=self.site_a,
            role=UserRole.GENERAL_USER,
            login_id='contract-general-user',
            user_name='General User',
            password_hash=make_password('password'),
        )

    def _create_device(self, company, site, device_id):
        device = Device.objects.create(
            company=company,
            site=site,
            device_id=device_id,
            device_name=device_id,
            auth_id=f'{device_id}-auth',
            auth_password_hash=make_password('device-password'),
            input_type='json',
            csv_header_mode='header_exists',
        )
        DeviceColumn.objects.create(
            device=device,
            column_name='temp',
            display_name='Temperature',
            data_type='number',
            unit='C',
        )
        DeviceStatus.objects.create(company=company, site=site, device=device)
        return device

    def authenticate(self, user):
        token = ApiToken.objects.create(user=user, key=f'token-{user.pk}')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.key}')

    def authenticate_system_admin(self):
        self.authenticate(PlatformUser.objects.get(login_id='admin'))

    def test_device_detail_and_graph_use_device_id(self):
        self.authenticate_system_admin()

        detail_response = self.client.get('/api/v1/devices/CONTRACT-A')
        graph_response = self.client.get('/api/v1/devices/CONTRACT-A/graph')

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data['device_id'], 'CONTRACT-A')
        self.assertEqual(graph_response.status_code, 200)
        self.assertEqual(graph_response.data['device_id'], 'CONTRACT-A')

    def test_list_query_parameters_filter_results(self):
        self.authenticate_system_admin()

        response = self.client.get(
            '/api/v1/devices',
            {'company_id': self.company_a.pk, 'site_id': self.site_a.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['device_id'] for item in response.data], ['CONTRACT-A'])

    def test_company_admin_cannot_create_outside_company_or_system_admin(self):
        self.authenticate(self.company_admin)

        site_response = self.client.post(
            '/api/v1/sites',
            {'company': self.company_b.pk, 'site_name': 'Outside Site'},
            format='json',
        )
        user_response = self.client.post(
            '/api/v1/users',
            {
                'company': None,
                'site': None,
                'role': UserRole.SYSTEM_ADMIN,
                'login_id': 'forbidden-admin',
                'user_name': 'Forbidden Admin',
                'password': 'password',
                'status': 'active',
            },
            format='json',
        )

        self.assertEqual(site_response.status_code, 400)
        self.assertEqual(user_response.status_code, 400)

    def test_general_user_can_read_scoped_thresholds_but_cannot_write(self):
        Threshold.objects.create(
            company=self.company_a,
            site=self.site_a,
            device=self.device_a,
            column_name='temp',
            threshold_name='Temperature threshold',
            upper_limit=25,
            notify_emails='',
        )
        self.authenticate(self.general_user)

        list_response = self.client.get('/api/v1/thresholds')
        create_response = self.client.post('/api/v1/thresholds', {}, format='json')

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(create_response.status_code, 403)

    def test_latest_response_contains_specified_fields_and_typed_values(self):
        self.device_a.communication_status.last_received_at = timezone.now()
        self.device_a.communication_status.estimated_interval_seconds = 3600
        self.device_a.communication_status.save()
        LatestValue.objects.create(
            company=self.company_a,
            site=self.site_a,
            device=self.device_a,
            column_name='temp',
            raw_value='20',
            display_value='20.0',
            device_timestamp=timezone.now(),
            server_timestamp=timezone.now(),
        )
        Threshold.objects.create(
            company=self.company_a,
            site=self.site_a,
            device=self.device_a,
            column_name='temp',
            threshold_name='Temperature threshold',
            upper_limit=25,
            notify_emails='',
        )
        self.authenticate(self.general_user)

        response = self.client.get('/api/v1/devices/latest')

        self.assertEqual(response.status_code, 200)
        item = response.data[0]
        self.assertEqual(item['estimated_interval_seconds'], 3600)
        self.assertEqual(item['communication_status'], 'online')
        self.assertEqual(item['alert_status'], 'normal')
        self.assertEqual(item['latest_values'][0]['display_name'], 'Temperature')
        self.assertEqual(item['latest_values'][0]['unit'], 'C')
        self.assertEqual(item['latest_values'][0]['display_value'], 20.0)

    def test_audit_log_response_uses_specified_field_names(self):
        admin = PlatformUser.objects.get(login_id='admin')
        AuditLog.objects.create(
            user=admin,
            company=self.company_a,
            site=self.site_a,
            action='update_device',
            target_type='Device',
            target_id=str(self.device_a.pk),
        )
        self.authenticate(admin)

        response = self.client.get('/api/v1/audit-logs')

        self.assertEqual(response.status_code, 200)
        item = response.data[0]
        self.assertIn('created_at', item)
        self.assertEqual(item['changed_by_login_id'], 'admin')
        self.assertEqual(item['target_type'], 'device')
        self.assertEqual(item['target_display_name'], 'CONTRACT-A')
        self.assertEqual(item['company_id'], self.company_a.pk)
        self.assertEqual(item['site_id'], self.site_a.pk)

    def test_password_reset_execute_updates_password(self):
        response = self.client.post(
            '/api/v1/auth/password-reset/execute',
            {
                'login_id': self.general_user.login_id,
                'new_password': 'new-password',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.general_user.refresh_from_db()
        self.assertTrue(check_password('new-password', self.general_user.password_hash))

    def test_json_device_defaults_csv_header_mode(self):
        self.authenticate_system_admin()

        response = self.client.post(
            '/api/v1/devices',
            {
                'company': self.company_a.pk,
                'site': self.site_a.pk,
                'device_id': 'CONTRACT-JSON',
                'device_name': 'JSON Device',
                'auth_id': 'contract-json-auth',
                'auth_password': 'device-password',
                'input_type': 'json',
                'columns': [
                    {
                        'column_name': 'temp',
                        'display_name': 'Temperature',
                        'data_type': 'number',
                        'unit': 'C',
                        'weight': 1,
                        'display_order': 1,
                    }
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['csv_header_mode'], 'header_exists')

    def test_unspecified_patch_and_delete_methods_are_rejected(self):
        self.authenticate_system_admin()

        patch_response = self.client.patch(
            f'/api/v1/sites/{self.site_a.pk}',
            {'site_name': 'Patched'},
            format='json',
        )
        delete_response = self.client.delete(
            f'/api/v1/devices/{self.device_a.device_id}',
        )

        self.assertEqual(patch_response.status_code, 405)
        self.assertEqual(delete_response.status_code, 405)

# Create your tests here.
