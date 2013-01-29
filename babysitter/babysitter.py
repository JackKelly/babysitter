#! /usr/bin/python

from __future__ import print_function, division
import time
import datetime
import logging, logging.handlers
import subprocess
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from abc import ABCMeta, abstractproperty
import xml.etree.ElementTree as ET # for XML parsing
import signal
import re
import sys

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

UPDATE_PERIOD = 10 # seconds

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
        return html_to_text(self.state_as_html)
    
    @property
    def state_as_html(self):
        return ['<span style=\"color:red\">FAIL</span>',
                '<span style=\"color:green\">OK</span>'][self.state]
    
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
    
    def extra_text(self):
        return ""
    
    def __str__(self):
        return html_to_text(self.html())

    def html(self):
        return '{}={}{}'.format(self.name.rpartition('/')[2], # remove path
                                self.state_as_html,
                                self.extra_text())


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
            p=subprocess.Popen(self.restart_command.split(), stderr=subprocess.PIPE)
        except Exception:
            logger.exception("Failed to restart. {}".format(self))
        else:
            if p.poll(): # process has already terminated
                logger.warn("Process {} has terminated already. stderr={}".format(self, p.stderr.read()))
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

    @property
    def state(self):
        return self.seconds_since_modified < self.timeout     

    @property
    def seconds_since_modified(self):
        return time.time() - self.last_modified

    @property        
    def last_modified(self):
        return os.path.getmtime(self.name)
    
    def extra_text(self):
        if self.appliance:
            msg = ", {}".format(self.appliance)
        else:
            msg = ""
        msg += ", last modified {:.1f}s ago.".format(
                                               self.seconds_since_modified)
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
    
    def extra_text(self):
        msg = ", size {} bytes.".format(self.size)
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
        if ((datetime.datetime.now() - self.initial_time).total_seconds() > UPDATE_PERIOD
            and self.space_decay_rate < 0):
            secs_until_full = self.available_space / -self.space_decay_rate 
            return datetime.timedelta(seconds=secs_until_full)
    
    def extra_text(self):
        msg = ", remaining={:.0f} MB".format(self.available_space)
        
        if self.time_until_full:
            msg += (", time until full={:d}days {:d}hrs {:d}mins"
                    .format(self.time_until_full.days,
                            self.time_until_full.seconds // 3600, 
                            self.time_until_full.seconds //   60))
            msg += ", full on {}".format((datetime.datetime.now() + self.time_until_full)
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

    
class Manager(object):
    """Manages multiple Checker objects."""
    
    def __init__(self):
        self._checkers = []
        self._heartbeat = {}
        
    def append(self, checker):
        self._checkers.append(checker)
        logger.info('Added {} to Manager: {}'.format(checker.__class__.__name__,
                                                     self._checkers[-1]))
        
    def run(self):
        self._send_heartbeat()
        
        while True:       
            html = ""
            for checker in self._checkers:
                if checker.just_changed_state:
                    html += "<h2>STATE CHANGED:</h2>\n"
                    html += "<p>" + checker.html() + "</p>\n"
                    if isinstance(checker, Process) and checker.state == FAIL:
                        html += "<p>Attempting to restart...</p>\n"
                        checker.restart()
                        time.sleep(5)
                        html += "<p>" + checker.html() + "</p>\n"

            if html:
                html += "<h2>CURRENT STATE OF ALL CHECKERS:</h2>\n" + self.html()
                self.send_email_with_time(html=html, subject="babysitter errors.")
    
            if self._need_to_send_heartbeat():
                self._send_heartbeat()
    
            time.sleep(UPDATE_PERIOD)
    
    def _need_to_send_heartbeat(self):
        if not self._heartbeat:
            return False
        
        now_hour = datetime.datetime.now().hour
        need_to_send = (now_hour == self._heartbeat['hour'] and
                self._heartbeat['last_checked'] != self._heartbeat['hour'])
        self._heartbeat['last_checked'] = now_hour
        return need_to_send
    
    def _send_heartbeat(self):
        msg = None
        if self._heartbeat.get('cmd'):
            logger.info("Attempting to run heartbeat command {}"
                        .format(self._heartbeat['cmd']))
            try:
                p=subprocess.Popen(self._heartbeat['cmd'].split(), stderr=subprocess.PIPE)
                p.wait()
            except Exception:
                msg = "<p><span style=\"color:red\">Failed to run {}</span></p>\n".format(self._heartbeat['cmd'])
                logger.exception(html_to_text(msg))
            else:
                if p.returncode == 0:
                    msg = "<p>Successfully ran {}</p>\n".format(self._heartbeat['cmd'])
                    logger.info(html_to_text(msg))
                else:
                    msg = "<p><span style=\"color:red\">Failed to run {}<br/>\n".format(self._heartbeat['cmd'])
                    msg += "stderr: {}</span></p>\n".format(p.stderr.read())
                    logger.warn(html_to_text(msg))

        msg = self.html() + msg

        self._email_html_file(subject='Babysitter heartbeat', 
                              filename=self._heartbeat.get('html_file'),
                              extra_text=msg)
            
    def load_config(self, config_file):
        try:
            config_tree = ET.parse(config_file)
        except IOError:
            msg = "Cannot open {}".format(config_file)
            logger.critical(msg)
            sys.exit(msg)

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
            
        # Load powerdata
        powerdata_etree = config_tree.findall("powerdata")
        for pd in powerdata_etree:
            self._load_powerdata(pd.findtext("dir"),
                                 pd.findtext("numeric_subdirs"),
                                 int(pd.findtext("timeout")))
        
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
        
        # Load heartbeat
        heartbeat_etree = config_tree.find("heartbeat")
        if heartbeat_etree is not None:
            self._heartbeat['hour'] = int(heartbeat_etree.findtext("hour"))
            self._heartbeat['cmd'] = heartbeat_etree.findtext("cmd")
            self._heartbeat['html_file'] = heartbeat_etree.findtext("html_file")
            self._heartbeat['last_checked'] = datetime.datetime.now().hour
        
    def _load_powerdata(self, directory, numeric_subdirs, timeout):
        logger.info("Loading powerdata")
        
        # Process directory to create a proper data_dir
        if directory[0] == "$":
            data_dir = os.environ.get(directory[1:])
            if not data_dir:
                log.critical("Environment variable {} not set".format(directory))
                sys.exit(1)
        else:
            data_dir = directory
            
        data_dir = os.path.realpath(data_dir)
        
        # process numeric_subdirs
        if numeric_subdirs.upper() == "TRUE":
            # find the highest number data_dir
            existing_subdirs = os.walk(data_dir).next()[1]
            if existing_subdirs:
                existing_subdirs.sort()
                data_dir += "/" + existing_subdirs[-1]
                
        # load all file_names in data_dir, using names from labels.dat
        file_names = os.walk(data_dir).next()[2]
        print("*********")
        print(file_names)
        
        # load labels
        labels = {}
        if "labels.dat" in file_names:
            logger.info("Opening labels.dat file")
            file_names.remove("labels.dat")
            with open(data_dir + "/labels.dat") as labels_file:
                lines = labels_file.readlines()
                
            for line in lines:
                line = line.split()
                labels[int(line[0])] = line[1]
            
        for file_name in file_names:
            chan = file_name.replace("channel_", "").replace(".dat", "")
            chan = int(chan)
            if labels and labels.get(chan):
                label = labels.get(chan)
            else:
                label = ""
            self.append(File(data_dir + "/" + file_name, timeout, label))
        
    def _email_html_file(self, subject, filename, extra_text=None):
            
        # Load the HTML filename as an ElementTree so we can extract
        # all images and modify the src to include "cid:"
        try:
            tree = ET.parse(filename)
        except:
            logger.warn("Failed to open filename {}".format(filename))
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
            logger.info("Not sending email because no SMTP server configured")
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
                    logger.warn("Can't open image file {}".format(img_filename))
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
                logger.exception("SMTP authentication error. Please check username and password in config file.")
                raise
            else:
                logger.info("Successfully sent message")
                break
        
    def __str__(self):
        msg = ""
        for checker in self._checkers:
            msg += '{}\n'.format(checker)
        return msg
    
    def html(self):
        msg = "<ul>\n"
        for checker in self._checkers:
            msg += '  <li>{}</li>\n'.format(checker.html())
        msg += "</ul>\n"
        return msg
    
    def shutdown(self):
        print("\nSHUT DOWN")
        if self.__dict__.get("SMTP_SERVER"):
            html = "<p>Babysitter SHUTTING DOWN.</p>{}\n".format(self.html())
            self.send_email_with_time(html=html, subject="babysitter.py shutting down")        


def _init_logger():
    global logger

    # create logger
    logger = logging.getLogger("babysitter")
    logger.setLevel(logging.DEBUG)

    # create console handler for stderr
    ch_stderr = logging.StreamHandler()
    ch_stderr.setLevel(logging.INFO)
    stderr_formatter = logging.Formatter('%(asctime)s %(levelname)s '
                        '%(message)s', datefmt="%y-%m-%d %H:%M:%S")
    ch_stderr.setFormatter(stderr_formatter)
    logger.addHandler(ch_stderr)
    
    # create file handler for babysitter.log
    logfile = os.path.dirname(os.path.realpath(__file__)) + "/../babysitter.log"
    fh = logging.handlers.RotatingFileHandler(logfile, maxBytes=1E6, backupCount=5)
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter('%(asctime)s level=%(levelname)s: '
                        'function=%(funcName)s, thread=%(threadName)s'
                        '\n   %(message)s')
    fh.setFormatter(fh_formatter)    
    logger.addHandler(fh)


def _shutdown():
    logger.info("Shutting down.")
    logging.shutdown() 
        

def main():
    
    _init_logger()
    logger.debug('MAIN: babysitter.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    # register SIGINT and SIGTERM handler
    logger.info("MAIN: setting signal handlers")

    # Python registers SIGINT but not SIGTERM. So use the same
    # sig handler for SIGINT for SIGTERM.  This allows us to 
    # clean up even when the code is terminated with kill or killall.
    signal.signal(signal.SIGTERM, signal.getsignal(signal.SIGINT))

    # Wrap manager.run() in a "try... except" block so we
    # can gracefully catch KeyboardInterrupt exceptions or we
    # can send any unexpected exceptions to logger.
    try:
        manager = Manager()
        manager.load_config(os.path.dirname(os.path.realpath(__file__)) 
                            + "/../babysitter_config.xml")    
        manager.run()
    except KeyboardInterrupt:
        manager.shutdown()
        _shutdown()
    except SystemExit, e:
        _shutdown()
        sys.exit(e)    
    except:
        logger.exception("")
        _shutdown()
        raise
    

if __name__ == "__main__":
    main()
