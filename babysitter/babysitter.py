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

UPDATE_PERIOD = 10 # seconds

class Checker:
    """Abstract base class (ABC) for classes which check on the state of
    a particular part of the system. """    
    
    __metaclass__ = ABCMeta

    def __init__(self, name):
        self.name = name
        self.last_state = self.state()

    @abstractmethod
    def state(self):
        pass
    
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
    def __init__(self, name, timeout=120, label=""):
        """File constructor
        
        Args:
            name (str) : including full path
            timeout (int or str) : time in seconds after which this file is 
                considered overdue.
        """
        self.timeout = int(timeout)
        self.appliance = label        
        super(File, self).__init__(name)

    def state(self):
        return self.seconds_since_modified() < self.timeout     

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


class HeartBeat(object):
    def __init__(self):
        self.hour = None
        self.cmd = None
        self.html_file = None
        self.last_checked = datetime.datetime.now().hour

    
class Manager(object):
    """Manages multiple Checker objects."""
    
    def __init__(self):
        self.checkers = []
        self.heartbeat = HeartBeat()
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
        self._send_heartbeat()
        
        while True:       
            html = ""
            for checker in self.checkers:
                if checker.just_changed_state():
                    if not html:
                        html += "<h2>STATE CHANGED:</h2>\n<ul>"
                    html += "<li>" + checker.html() + "</li>\n"
                    if isinstance(checker, Process) and checker.state() == FAIL:
                        html += "<p>Attempting to restart...</p>\n"
                        checker.restart()
                        time.sleep(5)
                        html += "<p>" + checker.html() + "</p>\n"

            if html:
                html += "</ul>\n<h2>CURRENT STATE OF ALL CHECKERS:</h2>\n" + self.html()
                self.send_email_with_time(html=html,
                                          subject="Babysitter detected state change.")
    
            if self._need_to_send_heartbeat():
                self._send_heartbeat()
    
            time.sleep(UPDATE_PERIOD)
    
    def _need_to_send_heartbeat(self):
        if not self.heartbeat:
            return False
        
        now_hour = datetime.datetime.now().hour
        need_to_send = (now_hour == self.heartbeat.hour and
                self.heartbeat.last_checked != self.heartbeat.hour)
        self.heartbeat.last_checked = now_hour
        return need_to_send
    
    def _send_heartbeat(self):
        msg = ""
        if self.heartbeat.cmd:
            log.info("Attempting to run heartbeat command {}"
                        .format(self.heartbeat.cmd))
            try:
                p = subprocess.Popen(self.heartbeat.cmd.split(), stderr=subprocess.PIPE)
                p.wait()
            except Exception:
                msg = "<p><span style=\"color:red\">Failed to run {}</span></p>\n".format(self.heartbeat.cmd)
                log.exception(html_to_text(msg))
            else:
                if p.returncode == 0:
                    msg = "<p>Successfully ran {}</p>\n".format(self.heartbeat.cmd)
                    log.info(html_to_text(msg))
                else:
                    msg = "<p><span style=\"color:red\">Failed to run {}<br/>\n".format(self.heartbeat.cmd)
                    msg += "stderr: {}</span></p>\n".format(p.stderr.read())
                    log.warn(html_to_text(msg))

        msg = self.html() + msg

        self._email_html_file(subject='Babysitter heartbeat', 
                              filename=self.heartbeat.html_file,
                              extra_text=msg)
        
    def load_powerdata(self, directory, numeric_subdirs, timeout):
        log.info("Loading powerdata... waiting 10 seconds for labels.dat")
        
        # Instantiate data_dir
        if not directory:
            log.critical("Directory for power data not set".format(directory))
            sys.exit(1)
            
        data_dir = os.path.realpath(directory)
        
        if not os.path.isdir(data_dir):
            log.critical("{} is not a directory!".format(data_dir))
            sys.exit(1)
        
        # process numeric_subdirs
        if numeric_subdirs:
            # find the highest number data_dir
            existing_subdirs = os.walk(data_dir).next()[1]
            if existing_subdirs:
                existing_subdirs.sort()
                data_dir += "/" + existing_subdirs[-1]
                
        log.info("data_dir = {}".format(data_dir))
        
        # load labels
        labels_filename = data_dir + "/labels.dat"
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
                    log.info("Failed to open labels.dat. Retry {}/{}".format(retry+2, MAX_RETRIES))
                    time.sleep(1)
            else:
                lines = labels_file.readlines()
                labels_file.close()
                for line in lines:
                    line = line.split()
                    chan = int(line[0])
                    label = line[1]
                    file_name = data_dir + "/channel_{:d}.dat".format(chan)
                    self.append(File(file_name, timeout, label))
                break
        
    def _email_html_file(self, subject, filename, extra_text=None):
            
        # Load the HTML filename as an ElementTree so we can extract
        # all images and modify the src to include "cid:"
        try:
            tree = ET.parse(filename)
        except:
            log.warn("Failed to open filename {}".format(filename))
            extra_text += "<p><span style=\"color:red\">Failed to open filename {}</span></p>".format(filename)
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
        msg['To'] = self.EMAIL_TO
        
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
                s.sendmail(me, [self.EMAIL_TO], msg.as_string())
                log.debug("quit")
                s.quit()
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
                log.exception("")
                time.sleep(2)
            except smtplib.SMTPAuthenticationError:
                log.exception("SMTP authentication error. Please check username and password in config file.")
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
        msg = "<ul>\n"
        for checker in self.checkers:
            msg += '  <li>{}</li>\n'.format(checker.html())
        msg += "</ul>\n"
        return msg
    
    def shutdown(self):
        log.info("Shutting down!")
        if self.__dict__.get("SMTP_SERVER"):
            html = "<p>Babysitter SHUTTING DOWN.</p>{}\n".format(self.html())
            self.send_email_with_time(html=html, subject="babysitter.py shutting down")
        logging.shutdown() 
                  
