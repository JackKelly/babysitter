from __future__ import print_function
import babysitter
import unittest
import StringIO
import datetime

class TestLoadConfig(unittest.TestCase):

    def setUp(self):
        # TODO: setup logger if necessary
        self.manager = babysitter.Manager()

    def test_file(self):
        self.manager.append(babysitter.File(name="/tmp", timeout=1000000))
        self.assertIsInstance(self.manager.checkers[0], babysitter.File)
        self.assertEqual(self.manager.checkers[0].name, '/tmp')
        self.assertEqual(self.manager.checkers[0].timeout, 1000000)
        self.assertTrue(self.manager.checkers[0].state() == babysitter.OK)        

    def test_process(self):
        self.manager.append(babysitter.Process(name="init", restart_command="sudo service init restart"))
        self.assertIsInstance(self.manager.checkers[0], babysitter.Process)
        self.assertEqual(self.manager.checkers[0].name, 'init')
        self.assertEqual(self.manager.checkers[0].restart_command,
                         'sudo service init restart')
        self.assertTrue(self.manager.checkers[0].state() == babysitter.OK)

    def test_disk_space(self):
        self.manager.append(babysitter.DiskSpaceRemaining(threshold=20, path="/"))
        self.assertIsInstance(self.manager.checkers[0], babysitter.DiskSpaceRemaining)        
        self.assertEqual(self.manager.checkers[0].threshold, 20)
        self.assertEqual(self.manager.checkers[0].path, "/")
        self.assertTrue(self.manager.checkers[0].state() == babysitter.OK)
        
    def test_time_until_full(self):
        self.manager.append(babysitter.DiskSpaceRemaining(threshold=20, path="/"))

        # Fake parameters so it looks like we're using 0.1MB per second
        self.manager.checkers[0].initial_space_remaining = \
            self.manager.checkers[0].available_space() + 0.1
            
        self.manager.checkers[0].initial_time = \
            datetime.datetime.now() - datetime.timedelta(seconds=1)

        self.assertAlmostEqual(self.manager.checkers[0].space_decay_rate(), -0.1, 1)
        
        print(self.manager.checkers[0])                
        
    def test_heartbeat(self):
        self.manager.heartbeat.hour = 8
        self.manager.heartbeat.cmd = "ls"
        self.manager.heartbeat.html_file = "index.html"
        self.assertEqual(self.manager.heartbeat.hour, 8)
        self.assertEqual(self.manager.heartbeat.cmd, "ls")
        self.assertEqual(self.manager.heartbeat.html_file, "index.html")
        self.assertEqual(self.manager.heartbeat.last_checked, datetime.datetime.now().hour)
        
        self._run_heartbeat_tests()
        
    def test_heartbeat_just_hour(self):
        self.manager.heartbeat.hour = 8
        self.assertEqual(self.manager.heartbeat.hour, 8)
        
        self._run_heartbeat_tests()
    
    def _run_heartbeat_tests(self):
        # test need_to_send by mocking up times
        self.manager.heartbeat.hour = datetime.datetime.now().hour
        self.manager.heartbeat.last_checked = datetime.datetime.now().hour-1
        self.assertTrue( self.manager._need_to_send_heartbeat() )
        self.assertFalse( self.manager._need_to_send_heartbeat() )
        
        # test _send_heartbeat
        self.manager._send_heartbeat()    
    
    def test_none(self):
        self.assertFalse( self.manager._need_to_send_heartbeat() )        

if __name__ == '__main__':
    unittest.main()
