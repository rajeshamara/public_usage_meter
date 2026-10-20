"""Database safety and durable outage queue tests; no real server writes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import pg_storage

class StorageTests(unittest.TestCase):
    def test_outage_preserves_event_and_display(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pg_storage,'ROOT',Path(folder)), patch.object(pg_storage,'connect',side_effect=OSError('offline')):
            s=pg_storage.Store()
            payload={'rateLimits':{'primary':{'usedPercent':1,'resetsAt':123,'windowDurationMins':10080}}}
            s.save('limits',payload)
            self.assertEqual(s.latest('limits')[1],payload)
            self.assertEqual(s.rows(123)[0][1],1)
            files=list(s.pending.glob('*.json'))
            self.assertEqual(len(files),1)
            self.assertEqual(json.loads(files[0].read_text())['payload'],payload)
            self.assertIsNotNone(s.storage_error)
            restarted=pg_storage.Store()
            self.assertEqual(restarted.latest('limits')[1],payload)
    def test_wrong_target_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pg_storage,'ROOT',Path(folder)):
            root=Path(folder)
            (root/'db_config.json').write_text(json.dumps({'database':'unrelated_application'}))
            with self.assertRaisesRegex(RuntimeError,'target guard'):pg_storage.settings()


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.root_patch = patch.object(pg_storage, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.env_patch = patch.dict(os.environ, {'USAGE_METER_DB_PASSWORD': 'synthetic-test-value'}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.cfg = {
            'host': 'database.example.test',
            'port': 55432,
            'database': 'usage_meter',
            'user': 'usage_meter_test',
            'expected_server_address': '192.0.2.20',
            'expected_server_port': 5432,
            'sslmode': 'require',
        }
        self.write_config()

    def write_config(self):
        (self.root / 'db_config.json').write_text(json.dumps(self.cfg), encoding='utf-8')

    def test_endpoint_is_configurable_and_backend_port_is_explicit(self):
        self.assertEqual(pg_storage.settings(), self.cfg)

    def test_incomplete_or_invalid_target_fails_before_connecting(self):
        for field, value in (
            ('database', 'unrelated_application'),
            ('host', ''),
            ('user', ''),
            ('port', 0),
            ('port', True),
            ('expected_server_port', 65536),
            ('expected_server_address', 'not-an-ip-address'),
            ('sslmode', 'not-a-tls-mode'),
        ):
            with self.subTest(field=field, value=value):
                invalid = dict(self.cfg, **{field: value})
                (self.root / 'db_config.json').write_text(json.dumps(invalid))
                with patch.object(pg_storage.psycopg, 'connect') as dial:
                    with self.assertRaises(RuntimeError):
                        pg_storage.connect()
                    dial.assert_not_called()

    def test_password_must_come_from_environment(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(pg_storage.psycopg, 'connect') as dial:
            with self.assertRaisesRegex(RuntimeError, 'USAGE_METER_DB_PASSWORD'):
                pg_storage.connect()
            dial.assert_not_called()
        for field in ('password', 'shared_config'):
            with self.subTest(field=field):
                forbidden = dict(self.cfg, **{field: 'unused-test-value'})
                (self.root / 'db_config.json').write_text(json.dumps(forbidden))
                with self.assertRaisesRegex(RuntimeError, 'credential files'):
                    pg_storage.settings()

    def test_verified_connection_uses_config_and_environment(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = (
            'usage_meter', 'usage_meter_test', '192.0.2.20', 5432)
        with patch.object(pg_storage.psycopg, 'connect', return_value=connection) as dial:
            self.assertIs(pg_storage.connect(), connection)
        self.assertEqual(dial.call_args.kwargs['host'], 'database.example.test')
        self.assertEqual(dial.call_args.kwargs['port'], 55432)
        self.assertEqual(dial.call_args.kwargs['password'], 'synthetic-test-value')
        self.assertEqual(dial.call_args.kwargs['sslmode'], 'require')
        connection.close.assert_not_called()

    def test_mismatched_identity_is_closed_before_returning(self):
        correct = ('usage_meter', 'usage_meter_test', '192.0.2.20', 5432)
        for index, replacement in enumerate(('other_database', 'other_role', '192.0.2.21', 5433)):
            with self.subTest(identity_field=index):
                identity = list(correct)
                identity[index] = replacement
                connection = Mock()
                connection.execute.return_value.fetchone.return_value = tuple(identity)
                with patch.object(pg_storage.psycopg, 'connect', return_value=connection):
                    with self.assertRaisesRegex(RuntimeError, 'identity'):
                        pg_storage.connect()
                connection.close.assert_called_once_with()
                self.assertEqual(connection.execute.call_count, 1)

    def test_identity_query_failure_closes_connection(self):
        connection = Mock()
        connection.execute.side_effect = RuntimeError('synthetic connection failure')
        with patch.object(pg_storage.psycopg, 'connect', return_value=connection):
            with self.assertRaisesRegex(RuntimeError, 'synthetic connection failure'):
                pg_storage.connect()
        connection.close.assert_called_once_with()
