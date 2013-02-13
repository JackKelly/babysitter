from __future__ import print_function, division
import time
import datetime
import logging, logging.handlers
log = logging.getLogger("babysitter")
import subprocess
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from abc import ABCMeta, abstractmethod
import xml.etree.ElementTree as ET # for XML parsing
import signal
import re
import sys
import atexit

"""
***********************************
********* DESCRIPTION *************
    
Script for monitoring disk space, multiple files and multiple processes.
If errors are found then an email is sent.
     

***********************************
********* REQUIREMENTS ************ 
    
EMAIL CONFIG
------------

Create an email_config.py in the following format:

SMTP_SERVER = "smtp.mydomain.com"
EMAIL_FROM  = "logger@mydomain.com"
EMAIL_TO    = ["me@me.com", "someone-else@them.com"]
USERNAME    = "smtp-username"
PASSWORD    = "let-me-in"

    
----------------------------------
If you want to be able to re-start the Network Time Protocol Daemon
if it fails then follow these steps:

SETUP SUDO FOR service restart ntp:
    
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

UPDATE_PERIOD = 10 # seconds

class Checker:
    """Abstract base class (ABC) for classes which check on the state of
    a particular part of the system. """    
    
    __metaclass__ = ABCMeta

    def __init__(self, name):
        self.name = name
        self.update_last_state()

    @abstractmethod
    def state(self):
        pass
    
    def update_last_state(self):
        self.last_state = self.state()        
    
    def state_as_str(self):
        return html_to_text(self.state_as_html)
    
    def state_as_html(self):
        return ['<span style=\"color:red\">FAIL</span>',
                '<span style=\"color:green\">OK</span>'][self.state()]
    
    def just_changed_state(self):
        state = self.state() # cache to avoid probs if this changes under us        
        if state == self.last_state:
            return False
        elif state == FAIL:
            log.warning('state change to FAIL: {}'.format(self))            
        elif state == OK:
            log.info('state change: {}'.format(self))
        
        self.last_state = state
        return True
    
    def extra_text(self):
        return ""
    
    def __str__(self):
        return html_to_text(self.html())

    def html(self):
        return '{}={}{}'.format(self.name.rpartition('/')[2], # remove path
                                self.state_as_html(),
                                self.extra_text())


class Process(Checker):
    """Class for monitoring a unix process.
    
    Attributes:
        name (str): the process name as it appears in `ps -A`
        restart_command (str): the command used to restart this process            
    
    """

    def __init__(self, name, restart_command=None):
        """
        Args:
            name (str): the process name as it appears in `ps -A`
        """
        self.restart_command = restart_command
        super(Process, self).__init__(name)

    def pid(self):
        pid_string = subprocess.check_output(['pidof', '-x', self.name])
        return pid_string.strip()
    
    def restart(self):
        if self.restart_command is None:
            log.info("No restart string for {}".format(self.name))
            return
        
        if self.state() == OK:
            return
        
        log.info("Attempting to restart {}".format(self.name))
        try:
            p = subprocess.Popen(self.restart_command.split(),
                                 stderr=subprocess.PIPE)
        except Exception:
            log.exception("Failed to restart. {}".format(self))
        else:
            if p.poll(): # process has already terminated
                log.warn("Process {} has terminated already. stderr={}"
                            .format(self, p.stderr.read()))
            else:
                log.info("Successfully restarted. {}".format(self) )

    def state(self):
        try:
            self.pid()
        except subprocess.CalledProcessError:
            return FAIL
        else:
            return OK


class File(Checker):
    """
    Attributes:
        - timeout (int): time in seconds after which this file is 
                considered overdue.
        - appliance (str): label
        - dead_duration (float): number of seconds this file has been
                dead for.
    
    """
    
    def __init__(self, name, timeout=120, label=""):
        """File constructor
        
        Args:
            name (str) : including full path
            timeout (int or str) : time in seconds after which this file is 
                considered overdue.
        """
        self.timeout = int(timeout)
        self.appliance = label
        self.dead_duration = 0.0
        self.output_dead_duration = False
        super(File, self).__init__(name)
        
    # Override
    def just_changed_state(self):
        # Only output dead_duration if we've just gone from FAIL to OK
        self.output_dead_duration = self.state()==OK and self.last_state==FAIL
        return super(File, self).just_changed_state()

    def state(self):
        s = self.seconds_since_modified() < self.timeout
        if s == FAIL:
            self.dead_duration = self.seconds_since_modified()
        return s

    def seconds_since_modified(self):
        return time.time() - self.last_modified()

    def last_modified(self):
        try:
            t = os.path.getmtime(self.name)
        except OSError: # file not found
            t = 0
        return t
    
    def extra_text(self):
        msg = ""
        if self.appliance:
            msg += ", {}".format(self.appliance)
            
        if os.path.exists(self.name):
            msg += ", last modified {:.1f}s ago.".format(
                                               self.seconds_since_modified())
            if self.output_dead_duration:
                msg += " Was dead for {:.1f}s.".format(self.dead_duration)
        else:
            msg += ", does not exist!"
            
        return msg


class FileGrows(Checker):
    def __init__(self, name):
        """FileGrows constructor. If a file grows (such an an error log file)
        then state goes to FAIL.
        
        Args:
            name (str) : including full path
        """
        self.name = name
        self.last_size = self.size()
        super(FileGrows, self).__init__(name)

    def state(self):
        if self.size() == self.last_size:
            return OK
        else:
            self.last_size = self.size()
            return FAIL 
    
    def size(self):
        try:
            s = os.path.getsize(self.name)
        except OSError: # file doesn't exist
            s = 0
        return s 
    
    def extra_text(self):
        msg = ", size {} bytes.".format(self.size())
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
        self.initial_space_remaining = self.available_space()
        self.initial_time = datetime.datetime.now()
        super(DiskSpaceRemaining, self).__init__('disk space')
        
    def state(self):
        return self.available_space() > self.threshold 
        
    def available_space(self):
        """Returns available disk space in MBytes."""
        # From http://stackoverflow.com/a/787832/732596
        s = os.statvfs(self.path)
        return (s.f_bavail * s.f_frsize) / 1024**2
    
    def space_decay_rate(self):
        """Returns rate at which space is diminishing in MByte per second.
        -ve denotes decreasing disk space."""
        dt = (datetime.datetime.now() - self.initial_time).total_seconds() # delta t
        ds = self.available_space() - self.initial_space_remaining # delta space
        return ds / dt
    
    def time_until_full(self):
        """Returns time delta object for time until disk is full."""
        if ((datetime.datetime.now() - self.initial_time).total_seconds() > UPDATE_PERIOD
            and self.space_decay_rate() < 0):
            secs_until_full = self.available_space() / -self.space_decay_rate() 
            return datetime.timedelta(seconds=secs_until_full)
    
    def extra_text(self):
        msg = ", remaining={:.0f} MB".format(self.available_space())
        time_until_full = self.time_until_full()
        if time_until_full:
            msg += (", time until full={:d}days {:d}hrs {:d}mins"
                    .format(time_until_full.days,
                            time_until_full.seconds // 3600, 
                            time_until_full.seconds //   60))
            msg += ", full on {}".format((datetime.datetime.now() + time_until_full)
                                         .strftime("%d/%m/%y %H:%M"))

        return msg    


def html_to_text(html):
    # We could use ElementTree to convert from HTML to text
    # but ET doesn't format tables the way I'd
    # like them to be formatted.
    
    # Remove all unwanted white space
    html = re.sub(r'^( )*', '', html, flags=re.MULTILINE)
    html = re.sub(r'( )*$', '', html, flags=re.MULTILINE)
    html = html.replace("\n", "")
    
    # Add desired white space
    html = html.replace("</p>", "\n")
    html = html.replace("</li>", "\n") 
    html = html.replace("</tr>", "\n")   
    html = html.replace("</th>", " ")  
    html = html.replace("</td>", " ")  
    html = re.sub(r"</h[0-9]>", "\n", html)
    
    # Replace basic formatting
    html = html.replace("<li>", "* ")
    html = html.replace("<em>", "*")
    html = html.replace("</em>", "*")
    html = html.replace("<b>", "**")
    html = html.replace("<b>", "**")
    html = html.replace("<h1>", "# ")
    html = html.replace("<h2>", "## ")
    html = html.replace("<h3>", "### ")
    html = html.replace("<h4>", "#### ")
    html = html.replace("<h5>", "##### ")
    html = html.replace("<h6>", "###### ")
  
    # use regex to remove any other HTML tags
    html = re.sub("""</?[a-zA-Z0-9( =:'"_!.,+/;\(\))]*/?>""", "", html)
    
    return html


def text_to_html(text):
    html = "<p>" + text.replace("\n", "</p>\n<p>") + "</p"
    return html


def run_commands(commands):
    """Attempts to run a list of shell commands and returns stderr 
    and, optionally stdout.
    
    Args:
        - commands (list of two-item tuples).
          The two fields in each tuple are: 
            1. cmd (string): A shell command e.g. 'tail -f logfile.log'
            2. send_stdout (bool): Set to True if you always want the
               returned string to include stdout output from the command.
    
    Returns:
        An HTML-formatted string containing any stderr output the command
        generated plus and stdout output the command generated.
        stdout output is only output if 'send_stdout' is True
        or if an error occurred when running the command.
        
    """
    msg = ""
    for cmd, send_stdout in commands:        
        msg += "<hr>\n"
        log.info("Attempting to run command {}".format(cmd))
        try:
            p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            p.wait()
        except Exception:
            m = ("<h2 style=\"color:red\">Failed to run <code>{}</code></h2>\n"
                 .format(cmd))
            msg += m
            log.exception(html_to_text(m))
        else:
            if p.returncode == 0:
                m = "<h2>Successfully ran <code>{}</code></h2>\n".format(cmd)
                msg += m
                log.info(html_to_text(m))
            else:
                m = ("<h2 style=\"color:red\">Failed to run <code>{}</code>"
                     "</h2>\n".format(cmd))
                msg += m
                log.warn(html_to_text(m))

            stderr = p.stderr.read()
            stdout = p.stdout.read()                
            
            if (send_stdout or stderr) and stdout:
                msg += "<h3>stdout</h3>\n <pre>{}</pre>\n".format(stdout)
                
            if stderr:
                msg += "<h3 style=\"color:red\">stderr</h3>\n"
                msg += "<pre style=\"color:red\">{}</pre>\n".format(stderr)

    return msg


class HeartBeat(object):
    def __init__(self):
        self.hour = None
        self.cmds = [] # list of commands
        self.html_file = None
        self.last_checked = datetime.datetime.now().hour


class NewDataDirError(Exception):
    """Error raised when a new data directory has been found."""
    pass


class Manager(object):
    """Manages multiple Checker objects."""

    def __init__(self):
        self.checkers = []
        self.heartbeat = HeartBeat()
        self.base_data_dir = ""
        self.sub_data_dir = ""
        self.state_change_cmds = [] # Commands to run if state changes
        self.SMTP_SERVER = ""
        self.EMAIL_FROM  = ""
        self.EMAIL_TO    = ""
        self.USERNAME    = ""
        self.PASSWORD    = ""
        
        # Python registers SIGINT but not SIGTERM. So use the same
        # sig handler for SIGINT for SIGTERM.  This allows us to 
        # clean up even when the code is terminated with kill or killall.
        signal.signal(signal.SIGTERM, signal.default_int_handler)
        atexit.register(self.shutdown)
        
    def append(self, checker):
        self.checkers.append(checker)
        log.info('Added {} to Manager: {}'.format(checker.__class__.__name__,
                                                     self.checkers[-1]))
        
    def run(self):
        """The main loop.  This continually checks the state of each checker
        and sends an email if any checker changes state.  Also sends hearbeat.
        
        Raises:
            NewDataDirError: if a new data directory is identified.
        """
        
        # Loop through all checkers to do an initial state check
        for checker in self.checkers:
            checker.update_last_state()

        # Send initial heartbeat
        self._send_heartbeat()
        
        # Main loop
        while True:       
            html = ""
            at_least_one_state_changed = False
            for checker in self.checkers:
                if checker.just_changed_state():
                    at_least_one_state_changed = True                    
                    if not html:
                        html += "<h2>STATE CHANGED:</h2>\n<ul>"
                    html += "<li>" + checker.html() + "</li>\n"
                    if isinstance(checker, Process) and checker.state() == FAIL:
                        html += "<p>Attempting to restart...</p>\n"
                        checker.restart()
                        time.sleep(5)
                        html += "<p>" + checker.html() + "</p>\n"

            if at_least_one_state_changed:
                html += "</ul>\n" + self.html()
                html += run_commands(self.state_change_cmds)
                self.send_email_with_time(html=html,
                                          subject="Babysitter detected state change.")

            if self._need_to_send_heartbeat():
                self._send_heartbeat()

            # Check if a new data subdir has been created
            if self.base_data_dir and self.sub_data_dir:
                if self._find_last_subdir() != self.sub_data_dir:
                    self._send_heartbeat("<p>New subdir found so about to restart "
                                         "babysitter. Below are the last stats "
                                         "for the old data subdirectory.</p>\n")
                    atexit.register(None) # This is required because the likely
                    # result of raising a NewDataDirError is to del the existing
                    # Manager instance and start a new Manager instance. If
                    # we don't unregister self.shutdown() then the old
                    # manager instance will not be deleted from memory!
                    raise NewDataDirError()
    
            time.sleep(UPDATE_PERIOD)
    
    def _need_to_send_heartbeat(self):
        if not self.heartbeat:
            return False
        
        now_hour = datetime.datetime.now().hour
        need_to_send = (now_hour == self.heartbeat.hour and
                self.heartbeat.last_checked != self.heartbeat.hour)
        self.heartbeat.last_checked = now_hour
        return need_to_send
    
    def _send_heartbeat(self, additional_html=""):
        msg = additional_html
        msg += self.html()
        msg += run_commands(self.heartbeat.cmds)
        msg += "<hr>\n"
        self._email_html_file(subject='Babysitter heartbeat', 
                              filename=self.heartbeat.html_file,
                              extra_text=msg)    
    
    def load_powerdata(self, directory, numeric_subdirs, timeout):
        """
        Process a REDD-formatted power data directory (such as recorded by
        rfm_ecomanager_logger).
        
        Parameters:
            - directory (str): if numeric_subdirs==True then directory is the
              base directory containing numerically named data directories.
              Else directory is the full data directory.
              
            - numeric_subdirs (bool)
            
            - timeout (int)
        
        Returns the full data directory
        """
        
        log.info("Loading powerdata... waiting 10 seconds for labels.dat")
        # time.sleep(10)
        
        # Instantiate base_data_dir
        if not directory:
            log.critical("Directory for power data not set".format(directory))
            sys.exit(1)
            
        self.base_data_dir = os.path.realpath(directory)
        
        if not os.path.isdir(self.base_data_dir):
            log.critical("{} is not a directory!".format(self.base_data_dir))
            sys.exit(1)
        
        # process numeric_subdirs
        if numeric_subdirs:
            self.sub_data_dir = self._find_last_subdir()
                
        full_data_dir = self.base_data_dir
        if self.sub_data_dir:
            full_data_dir += "/" + self.sub_data_dir            
                
        log.info("full_data_dir = {}".format(full_data_dir))
        
        # load labels
        labels_filename = full_data_dir + "/labels.dat"
        log.info("Opening {} file".format(labels_filename))
        MAX_RETRIES = 10
        for retry in range(MAX_RETRIES):
            try:
                labels_file = open(labels_filename)
            except IOError: # file not found
                if retry == MAX_RETRIES-1: # run out of retries
                    log.critical("Failed to open labels.dat after {} attempts."
                                 .format(MAX_RETRIES))
                    sys.exit(1)
                else:
                    log.info("Failed to open labels.dat. Retry {}/{}"
                             .format(retry+2, MAX_RETRIES))
                    time.sleep(1)
            else:
                lines = labels_file.readlines()
                labels_file.close()
                for line in lines:
                    line = line.split()
                    chan = int(line[0])
                    label = line[1]
                    file_name = full_data_dir + "/channel_{:d}.dat".format(chan)
                    self.append(File(file_name, timeout, label))
                break

        return full_data_dir
    
    def _find_last_subdir(self):
        """Find the highest number self.base_data_dir
        
        Returns:
            String containing highest number subdir.
        """
        existing_subdirs = os.walk(self.base_data_dir).next()[1]
        if existing_subdirs:
            existing_subdirs.sort()
            return existing_subdirs[-1]
        
        
    def _email_html_file(self, subject, filename, extra_text=None):
            
        # Load the HTML filename as an ElementTree so we can extract
        # all images and modify the src to include "cid:"
        try:
            tree = ET.parse(filename)
        except:
            log.warn("Failed to open filename {}".format(filename))
            extra_text += ("<p><span style=\"color:red\">Failed to open "
                           "filename {}</span></p>".format(filename))
            self.send_email(subject, extra_text)
            return
        
        # Extract images
        directory = os.path.dirname(filename)        
        img_files = []
        for img in tree.getiterator('img'):
            img_file = os.path.join(directory, img.get('src'))
            img_files.append(img_file)
            img.set('src', "cid:"+img.get('src'))

        html = ET.tostring(tree.getroot())
        
        if extra_text:
            html = html.replace("<body>", "<body>\n{}".format(extra_text))
        
        self.send_email(subject, html, img_files)
        
    def send_email_with_time(self, subject, html):
        html += '<p>Unixtime = {}</p>\n'.format(time.time())       
        html = "<html>\n<head></head>\n<body>" + html + "</body>\n</html>\n"
        self.send_email(subject, html)        

    def send_email(self, subject, html, img_files=None):
        if not self.SMTP_SERVER:
            log.info("Not sending email because no SMTP server configured")
            return
                
        hostname = os.uname()[1]
        me = hostname + '<' + self.EMAIL_FROM + '>'
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] =  me
        msg['To'] = ", ".join(self.EMAIL_TO)
        
        msg.attach(MIMEText(html_to_text(html), 'plain'))        
        msg.attach(MIMEText(html, 'html'))        
    
        # Attach image files
        if img_files:
            for img_filename in img_files:
                try:
                    fp = open(img_filename, 'rb')
                except:
                    log.warn("Can't open image file {}".format(img_filename))
                else:
                    mime_img = MIMEImage(fp.read())
                    fp.close()
                    basename = os.path.basename(img_filename)
                    mime_img.add_header('Content-Disposition', 'attachment',
                                        filename=basename)
                    mime_img.add_header('Content-ID', '<' + basename + '>')
                    msg.attach(mime_img)
    
        # Send email to SMTP server. Retry if server disconnects.
        retries = 0
        while retries < 5:
            retries += 1
            try:
                log.debug("SMPT_SSL {}".format(self.SMTP_SERVER))
                s = smtplib.SMTP_SSL()
                s.connect(self.SMTP_SERVER)
                log.debug("logging in as {}".format(self.USERNAME))
                s.login(self.USERNAME, self.PASSWORD)
                log.debug("sendmail to {}".format(self.EMAIL_TO))
                s.sendmail(me, self.EMAIL_TO, msg.as_string())
                log.debug("quit")
                s.quit()
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
                log.exception("")
                time.sleep(2)
            except smtplib.SMTPAuthenticationError:
                log.exception("SMTP authentication error. Please check username and password in config file.")
                raise
            except:
                log.exception("Error while trying to send email")
                raise
            else:
                log.info("Successfully sent message")
                break
        
    def __str__(self):
        msg = ""
        for checker in self.checkers:
            msg += '{}\n'.format(checker)
        return msg
    
    def html(self):
        msg = "<h2>CURRENT STATE OF ALL CHECKERS:</h2>\n"
        if self.base_data_dir:
            msg += "<p>Data directory = " + self.base_data_dir
            msg += "/" + self.sub_data_dir + "</p>\n" 
        msg += "<ul>\n"
        for checker in self.checkers:
            msg += '  <li>{}</li>\n'.format(checker.html())
        msg += "</ul>\n"
        return msg
    
    def shutdown(self):
        log.info("Shutting down!")
        if self.__dict__.get("SMTP_SERVER"):
            html = "<p>Babysitter SHUTTING DOWN.</p>{}\n".format(self.html())
            html += run_commands(self.state_change_cmds)
            self.send_email_with_time(html=html, subject="babysitter.py shutting down")
        logging.shutdown() 
                  
