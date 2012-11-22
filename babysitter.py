#! /usr/bin/python

from __future__ import print_function, division
import time
import datetime
import logging
import subprocess
import os
import smtplib
from email.mime.text import MIMEText
from abc import ABCMeta, abstractproperty
import xml.etree.ElementTree as ET # for XML parsing
import signal

"""
***********************************
********* DESCRIPTION *************
    
    Script for monitoring disk space, multiple files and multiple processes.
    If errors are found then an email is sent.
    Configuration is stored in a babysitter_config.xml file.
    Copy babysitter_config.example.xml to babysitter_config.xml and edit.
     

***********************************
********* REQUIREMENTS ************ 
    
    ----------------------------------
    If you want to be able to re-start the Network Time Protocol Daemon
    if it fails then follow these steps:
    
    SETUP SUDO FOR service restart ntp
    ----------------------------------
        
        This Python script needs to be able to run 'service restart ntp'
        without requiring a password.  Follow these steps:
        
        1. run 'sudo visudo'
        2. add the following line: 
           'USER   ALL=NOPASSWD: /usr/sbin/service ntp *'
           (replace USER with your unix username!) 

"""

# Define some constants for tracking state
FAIL = 0
OK   = 1

class Checker:
    """Abstract base class (ABC) for classes which check on the state of
    a particular part of the system. """    
    
    __metaclass__ = ABCMeta

    def __init__(self, name):
        self.name = name
        self.last_state = self.state

    @abstractproperty
    def state(self):
        pass
    
    @property
    def state_as_str(self):
        return ['FAIL', 'OK'][self.state]
    
    @property
    def just_changed_state(self):
        state = self.state # cache to avoid probs if this changes under us        
        if state == self.last_state:
            return False
        elif state == FAIL:
            logger.warning('state change to FAIL: {}'.format(self))            
        elif state == OK:
            logger.info('state change: {}'.format(self))
        
        self.last_state = state
        return True
    
    def __str__(self):
        return '{}={}'.format(self.name.rpartition('/')[2], # remove path
                                self.state_as_str)
    

class Process(Checker):
    """Class for monitoring a unix process.
    
    Attributes:
        name (str): the process name as it appears in `ps -A`
        restart_command (str): the command used to restart this process            
    
    """

    def __init__(self, name):
        """
        Args:
            name (str): the process name as it appears in `ps -A`
        """
        self.restart_command = None
        super(Process, self).__init__(name)

    @property
    def pid(self):
        pid_string = subprocess.check_output(['pidof', '-x', self.name])
        return pid_string.strip()
    
    def restart(self):
        if self.restart_command is None:
            logger.info("No restart string for {}".format(self.name))
            return
        
        if self.state == OK:
            return
        
        logger.info("Attempting to restart {}".format(self.name))
        try:
            subprocess.Popen(self.restart_command.split())
        except Exception:
            logger.exception("Failed to restart. {}".format(self))
        else:
            logger.info("Successfully restarted. {}".format(self) )

    @property
    def state(self):
        try:
            self.pid
        except subprocess.CalledProcessError:
            return FAIL
        else:
            return OK


class File(Checker):
    def __init__(self, name, timeout=120):
        """File constructor
        
        Args:
            name (str) : including full path
            timeout (int or str) : time in seconds after which this file is 
                considered overdue.
        """
        self.timeout = int(timeout)
        super(File, self).__init__(name)

    @property
    def state(self):
        return self.seconds_since_modified < self.timeout     

    @property
    def seconds_since_modified(self):
        return time.time() - self.last_modified

    @property        
    def last_modified(self):
        return os.path.getmtime(self.name)
    
    def __str__(self):
        msg = super(File, self).__str__()
        msg += ", last modified {:.1f}s ago.".format(self.seconds_since_modified)
        return msg


class FileGrows(Checker):
    def __init__(self, name):
        """FileGrows constructor. If a file grows (such an an error log file)
        then state goes to FAIL.
        
        Args:
            name (str) : including full path
        """
        self.name = name
        self.last_size = self.size
        super(FileGrows, self).__init__(name)

    @property
    def state(self):
        if self.size == self.last_size:
            return OK
        else:
            self.last_size = self.size
            return FAIL 
    
    @property        
    def size(self):
        try:
            s = os.path.getsize(self.name)
        except OSError: # file doesn't exist
            s = 0
        return s 
    
    def __str__(self):
        msg = super(FileGrows, self).__str__()
        msg += ", size {} bytes.".format(self.size)
        return msg


class DiskSpaceRemaining(Checker):
    
    def __init__(self, threshold, path='/'):
        """
        Args:
            threshold (int or str): number of MBytes of free space below which
                state will change to FAIL.
        """
        self.threshold = int(threshold)
        self.path = path
        self.initial_space_remaining = self.available_space
        self.initial_time = datetime.datetime.now()
        super(DiskSpaceRemaining, self).__init__('disk space')
        
    @property
    def state(self):
        return self.available_space > self.threshold 
        
    @property
    def available_space(self):
        """Returns available disk space in MBytes."""
        # From http://stackoverflow.com/a/787832/732596
        s = os.statvfs(self.path)
        return (s.f_bavail * s.f_frsize) / 1024**2
    
    @property
    def space_decay_rate(self):
        """Returns rate at which space is diminishing in MByte per second.
        -ve denotes decreasing disk space."""
        dt = (datetime.datetime.now() - self.initial_time).total_seconds() # delta t
        ds = self.available_space - self.initial_space_remaining # delta space
        return ds / dt
    
    @property
    def time_until_full(self):
        """Returns time delta object for time until disk is full."""
        secs_until_full = -self.space_decay_rate * self.available_space
        return datetime.timedelta(seconds=secs_until_full)
    
    def __str__(self):
        msg = super(DiskSpaceRemaining, self).__str__()
        msg += ", remaining={:.0f} MB".format(self.available_space)
        
        if (datetime.datetime.now() - self.initial_time).total_seconds() > 1:
            msg += (", time until full={:d}days {:d}hrs {:d}mins"
                    .format(self.time_until_full.days,
                            self.time_until_full.seconds // 3600, 
                            self.time_until_full.seconds //   60))
            msg += ", full on {}".format((datetime.datetime.now() + self.time_until_full)
                                         .strftime("%d/%m/%y %H:%M"))
        return msg    


class Manager(object):
    """Manages multiple Checker objects."""
    
    def __init__(self):
        self._checkers = []
        
    def append(self, checker):
        self._checkers.append(checker)
        logger.info('Added {} to Manager: {}'.format(checker.__class__.__name__,
                                                     self._checkers[-1]))
        
    def run(self):
        msg = "IAM logger babysitter running.\n{}".format(self)
        self.send_email(body=msg, subject="babysitter.py running")
        
        while True:
            msg = ""
            for checker in self._checkers:
                if checker.just_changed_state:
                    msg += "STATE CHANGED:\n"
                    msg += str(checker) + "\n"
                    if isinstance(checker, Process) and checker.state == FAIL:
                        msg += "Attempting to restart...\n"
                        checker.restart()
                        time.sleep(5)
                        msg += str(checker) + "\n"
                            
            if msg != "":
                msg += "CURRENT STATE OF ALL CHECKERS:\n" + str(self)
                self.send_email(body=msg, subject="iam_logger errors.")
    
            time.sleep(10)
            
    def load_config(self, config_file):
        config_tree = ET.parse(config_file)

        # Email config
        self.SMTP_SERVER = config_tree.findtext("smtp_server")
        self.EMAIL_FROM  = config_tree.findtext("email_from")
        self.EMAIL_TO    = config_tree.findtext("email_to")
        self.USERNAME    = config_tree.findtext("username")
        self.PASSWORD    = config_tree.findtext("password")
    
        logger.debug('\nSMTP_SERVER={}\nEMAIL_FROM={}\nEMAIL_TO={}'
                     .format(self.SMTP_SERVER, self.EMAIL_FROM, self.EMAIL_TO))
    
        # Disk space checker
        disk_space_threshold_etree = config_tree.find("disk_space")
        if disk_space_threshold_etree is not None:
            disk_space_threshold = disk_space_threshold_etree.findtext("threshold")
            mount_point = disk_space_threshold_etree.findtext("mount_point")
            self.append(DiskSpaceRemaining(disk_space_threshold, mount_point))
    
        # Load files
        files_etree = config_tree.findall("file")
        for f in files_etree:
            self.append(File(f.findtext('location'), 
                             int(f.findtext('timeout'))))
        
        # Load processes
        processes_etree = config_tree.findall("process")
        for process in processes_etree:
            p = Process(process.findtext('name'))
            p.restart_command = process.findtext('restart_command')
            self.append(p)
            
        # Load file grows
        filegrows_etree = config_tree.findall("filegrows")
        for f in filegrows_etree:
            if f.text is not None:
                self.append(FileGrows(f.text))
        

    def send_email(self, body, subject):
        if not self.SMTP_SERVER:
            logger.info("Not sending email because no SMTP server configured")
            return
        
        hostname = os.uname()[1]
        me = hostname + '<' + self.EMAIL_FROM + '>'
        body += '\nUnixtime = ' + str(time.time()) + '\n'       
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] =  me
        msg['To'] = self.EMAIL_TO
    
        logger.debug('sending message: \n{}'.format(msg.as_string()))
    
        retry = True
        while retry is True:
            try:
                logger.debug("SMPT_SSL")
                s = smtplib.SMTP_SSL(self.SMTP_SERVER)
                logger.debug("logging in")
                s.login(self.USERNAME, self.PASSWORD)
                
                logger.debug("sendmail")                
                s.sendmail(me, [self.EMAIL_TO], msg.as_string())
                logger.debug("quit")
                s.quit()
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
                logger.exception("")
                time.sleep(2)
            except smtplib.SMTPAuthenticationError:
                error_msg = "SMTP authentication error. Please check username and password in config file."
                print(error_msg)
                logger.exception(error_msg)
                raise
            else:
                logger.info("Successfully sent message")
                retry = False
        
    def __str__(self):
        msg = ""
        for checker in self._checkers:
            msg += '{}\n'.format(checker)
        return msg


def _init_logger():
    global logger

    # create logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # create console handler for stderr
    ch_stderr = logging.StreamHandler()
    ch_stderr.setLevel(logging.INFO)
    stderr_formatter = logging.Formatter('%(asctime)s %(levelname)s: '
                        '%(message)s', datefmt='%d/%m/%y %H:%M:%S')
    ch_stderr.setFormatter(stderr_formatter)
    logger.addHandler(ch_stderr)
    
    # create file handler for babysitter.log
    fh = logging.FileHandler('babysitter.log')
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter('%(asctime)s level=%(levelname)s: '
                        'function=%(funcName)s, thread=%(threadName)s'
                        '\n   %(message)s')
    fh.setFormatter(fh_formatter)    
    logger.addHandler(fh)


def _shutdown():
    logger.info("Shutting down.")
    logging.shutdown() 
        
        
def _signal_handler(signal_number, frame):
    raise KeyboardInterrupt()


def main():
    
    _init_logger()
    logger.debug('MAIN: babysitter.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    # register SIGINT and SIGTERM handler
    logger.info("MAIN: setting signal handlers")
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Wrap manager.run() in a "try... except" block so we
    # can gracefully catch KeyboardInterrupt exceptions or we
    # can send any unexpected exceptions to logger.
    try:
        manager = Manager()
        manager.load_config("babysitter_config.xml")    
        manager.run()
    except KeyboardInterrupt:
        _shutdown()
    except Exception:
        logger.exception("")
        _shutdown()
        raise
    

if __name__ == "__main__":
    main()
