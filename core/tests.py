import base64
import json

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import (
    Company,
    Device,
    DeviceColumn,
    DeviceStatus,
    LatestValue,
    ParsedData,
    RawData,
    Site,
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
            f'/api/v1/devices/{self.device.pk}/graph',
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

# Create your tests here.
