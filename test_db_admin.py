"""The psql helper uses the same configured target and TLS settings; no processes run."""
import os
import unittest
from unittest.mock import MagicMock, patch

import db_admin


class DatabaseHelperTests(unittest.TestCase):
    def test_query_preserves_target_and_read_only_guards(self):
        cfg = {
            'host': 'database.example.test',
            'port': 55432,
            'database': 'usage_meter',
            'user': 'usage_meter_test',
            'expected_server_address': '192.0.2.20',
            'expected_server_port': 5432,
            'sslmode': 'verify-full',
            'sslrootcert': '/example/ca.pem',
        }
        verified = MagicMock()
        with patch.object(db_admin, 'settings', return_value=cfg), \
             patch.object(db_admin, 'connect', return_value=verified) as preflight, \
             patch.object(db_admin, 'database_password', return_value='synthetic-test-value'), \
             patch.object(db_admin.shutil, 'which', return_value='/example/psql'), \
             patch.object(db_admin.subprocess, 'run') as run, \
             patch.dict(os.environ, {}, clear=True), patch('builtins.print'):
            db_admin.psql_file(db_admin.ROOT / 'sql/queries.sql', readonly=True)
        preflight.assert_called_once_with()
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-h') + 1], cfg['host'])
        self.assertEqual(command[command.index('-p') + 1], str(cfg['port']))
        self.assertEqual(command[command.index('-U') + 1], cfg['user'])
        self.assertEqual(command[command.index('-d') + 1], 'usage_meter')
        self.assertIn('--single-transaction', command)
        self.assertIn('ON_ERROR_STOP=1', command)
        guard = command[command.index('-c') + 1]
        self.assertIn("current_database() = 'usage_meter'", guard)
        self.assertIn("current_user = 'usage_meter_test'", guard)
        self.assertIn("inet_server_addr() = '192.0.2.20'::inet", guard)
        self.assertIn('inet_server_port() = 5432', guard)
        self.assertLess(command.index('-c'), command.index('-f'))
        env = run.call_args.kwargs['env']
        self.assertEqual(env['PGPASSWORD'], 'synthetic-test-value')
        self.assertEqual(env['PGSSLMODE'], 'verify-full')
        self.assertEqual(env['PGSSLROOTCERT'], '/example/ca.pem')
        self.assertIn('default_transaction_read_only=on', env['PGOPTIONS'])

    def test_sql_outside_project_directory_is_rejected(self):
        with patch.object(db_admin, 'connect') as preflight, \
             patch.object(db_admin.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'SQL file must be inside'):
                db_admin.psql_file(db_admin.ROOT.parent / 'unrelated.sql')
            preflight.assert_not_called()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
