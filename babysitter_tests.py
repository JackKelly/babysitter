from __future__ import print_function
import babysitter
import unittest
import StringIO
import datetime

class TestLoadConfig(unittest.TestCase):
    
    def setUp(self):
        babysitter._init_logger()
        self.manager = babysitter.Manager()
        
    def _load_config(self, xml):
        xml_as_file = StringIO.StringIO(xml)
        self.manager.load_config(xml_as_file)        
        
    def test_file(self):
        xml = """
        <config>
            <file>
              <location>/tmp</location>
              <timeout>1000000</timeout>
            </file>
        </config>
        """
        self._load_config(xml)
        self.assertIsInstance(self.manager._checkers[0], babysitter.File)
        self.assertEqual(self.manager._checkers[0].name, '/tmp')
        self.assertEqual(self.manager._checkers[0].timeout, 1000000)
        self.assertTrue(self.manager._checkers[0].state == babysitter.OK)        

    def test_process(self):
        xml = """
        <config>
            <process>
                <name>init</name>
                <restart_command>sudo service init restart</restart_command>
            </process>
        </config>
        """
        self._load_config(xml)
        self.assertIsInstance(self.manager._checkers[0], babysitter.Process)        
        self.assertEqual(self.manager._checkers[0].name, 'init')
        self.assertEqual(self.manager._checkers[0].restart_command,
                         'sudo service init restart')
        self.assertTrue(self.manager._checkers[0].state == babysitter.OK)

    def test_disk_space(self):
        xml = """
        <config>
            <disk_space>
                <threshold>20</threshold>
                <mount_point>/</mount_point>
            </disk_space>            
        </config>
        """
        self._load_config(xml)
        self.assertIsInstance(self.manager._checkers[0], babysitter.DiskSpaceRemaining)        
        self.assertEqual(self.manager._checkers[0].threshold, 20)
        self.assertEqual(self.manager._checkers[0].path, "/")
        self.assertTrue(self.manager._checkers[0].state == babysitter.OK)
        
    def test_time_until_full(self):
        xml = """
        <config>
            <disk_space>
                <threshold>20</threshold>
                <mount_point>/</mount_point>
            </disk_space>            
        </config>
        """
        self._load_config(xml)
        
        # Fake parameters so it looks like we're using 0.1MB per second
        self.manager._checkers[0].initial_space_remaining = self.manager._checkers[0].available_space + 0.1
        self.manager._checkers[0].initial_time = datetime.datetime.now() - datetime.timedelta(seconds=1)
        self.assertAlmostEqual(self.manager._checkers[0].space_decay_rate, -0.1, 1)
        
        print(self.manager._checkers[0])
                

    def test_email_config(self):
        xml = """
        <config>
            <smtp_server>mail.test.server</smtp_server>
            <email_from>test@email.address</email_from>
            <email_to>another@email.address</email_to>
        </config>
        """
        self._load_config(xml)
        self.assertEqual(self.manager.SMTP_SERVER, 'mail.test.server')
        self.assertEqual(self.manager.EMAIL_FROM, 'test@email.address')
        self.assertEqual(self.manager.EMAIL_TO, 'another@email.address')


if __name__ == '__main__':
    unittest.main()
