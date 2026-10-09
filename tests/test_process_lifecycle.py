import asyncio
import importlib
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from utils.process_lifecycle import ParentProcess, join_desktop_job


@unittest.skipUnless(sys.platform == 'win32', 'Windows process handles')
class WindowsParentTests(unittest.TestCase):
    def test_exited_parent_with_an_open_handle_is_not_alive(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        parent = ParentProcess(child.pid)
        self.addCleanup(parent.close)
        self.assertTrue(parent.alive())
        child.terminate()
        child.wait(timeout=5)
        # Popen and ParentProcess still hold handles to the terminated process.
        self.assertFalse(parent.alive())
        parent.close()
        parent.close()

    def test_missing_parent_is_not_alive(self):
        parent = ParentProcess(0xFFFFFFFE)
        self.addCleanup(parent.close)
        self.assertFalse(parent.alive())

    def test_missing_desktop_job_fails_before_services_start(self):
        with patch.dict(os.environ, {'TIPTUNE_WINDOWS_JOB_NAME': f'Local\\TipTune-missing-{os.getpid()}'}):
            with self.assertRaises(OSError):
                join_desktop_job()


class ParentWatchdogTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.TemporaryDirectory()
        root = Path(cls.runtime.name)
        cls.environment = patch.dict(os.environ, {'TIPTUNE_CONFIG': str(root / 'config.ini'),
            'TIPTUNE_CACHE_DIR': str(root / 'cache'), 'TIPTUNE_DEFAULT_LOG_PATH': str(root / 'test.log')})
        cls.environment.start()
        cls.app = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        logging.shutdown()
        cls.runtime.cleanup()

    def setUp(self):
        patcher = patch.object(self.app, 'shutdown_event', asyncio.Event())
        patcher.start(); self.addCleanup(patcher.stop)

    async def test_standalone_launch_has_no_parent_watchdog(self):
        with patch.dict(os.environ, {}, clear=True):
            await self.app._watch_parent_process()
        self.assertFalse(self.app.shutdown_event.is_set())

    async def test_parent_exit_requests_shutdown_and_closes_handle(self):
        parent = Mock(alive=Mock(return_value=False))
        with patch.dict(os.environ, {'TIPTUNE_PARENT_PID': '1234'}), patch.object(self.app, 'ParentProcess', return_value=parent):
            await asyncio.wait_for(self.app._watch_parent_process(), 1)
        self.assertTrue(self.app.shutdown_event.is_set())
        parent.close.assert_called_once()

    async def test_cancellation_closes_parent_handle(self):
        parent = Mock(alive=Mock(return_value=True))
        with patch.dict(os.environ, {'TIPTUNE_PARENT_PID': '1234'}), patch.object(self.app, 'ParentProcess', return_value=parent):
            task = asyncio.create_task(self.app._watch_parent_process())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        parent.close.assert_called_once()
        self.assertFalse(self.app.shutdown_event.is_set())


if __name__ == '__main__':
    unittest.main()
