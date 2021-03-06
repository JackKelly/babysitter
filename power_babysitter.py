#! /usr/bin/python
from __future__ import print_function, division
import logging.handlers
log = logging.getLogger("babysitter")
from babysitter import Manager, DiskSpaceRemaining, Process, NewDataDirError, File
import time, sys, inspect, os
import email_config

"""
This script is both an example of how to use babysitter
and also babysits rfm_ecomanager_logger.


REQUIREMENTS
============

EMAIL CONFIG
------------

Create an email_config.py in the following format:

SMTP_SERVER = "smtp.mydomain.com"
EMAIL_FROM  = "logger@mydomain.com"
EMAIL_TO    = ["me@me.com", "someone-else@them.com"]
USERNAME    = "smtp-username"
PASSWORD    = "let-me-in"


ENVIRONMENT VARIABLES
---------------------

The following environment variables must be set:
   DATA_DIR
   LOGGER_BASE_DIR

"""

FILE_PATH = os.path.dirname(inspect.getfile(inspect.currentframe()))

def init_logger():
    # create logger
    logger = logging.getLogger("babysitter")
    logger.setLevel(logging.DEBUG)

    # date formatting
    datefmt = "%y-%m-%d %H:%M:%S %Z"

    # create console handler (ch) for stdout
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch_formatter = logging.Formatter('%(asctime)s %(levelname)s '
                        '%(message)s', datefmt=datefmt)
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)
    
    # create file handler (fh) for babysitter.log
    logfile = os.path.join(FILE_PATH, "babysitter.log")
    fh = logging.handlers.RotatingFileHandler(logfile, maxBytes=1E7, backupCount=5)
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter("%(asctime)s %(levelname)s" 
                                     " %(funcName)s %(message)s",
                                     datefmt=datefmt)
    fh.setFormatter(fh_formatter)    
    logger.addHandler(fh)

def _set_config(manager):
    ########### EMAIL CONFIG ############################################
    manager.SMTP_SERVER = email_config.SMTP_SERVER
    manager.EMAIL_FROM  = email_config.EMAIL_FROM
    manager.EMAIL_TO    = email_config.EMAIL_TO
    manager.USERNAME    = email_config.USERNAME
    manager.PASSWORD    = email_config.PASSWORD

    ########### FILES ###################################################
    # manager.append(File(name="/path/to/file", timeout=120))
        
    ########### SET DATA_DIR ###############################################
    base_data_dir = os.environ.get("DATA_DIR")
    if not base_data_dir:
        log.critical("You must set the DATA_DIR environment variable")
        sys.exit(1)
        
    ########### DISK SPACE CHECKER ######################################
    manager.append(DiskSpaceRemaining(threshold=5000, path=base_data_dir))

    ########### PROCESSES ###############################################
    # Each process will be monitored.  If it dies then babysitter will attempt
    # to restart the process and message will be sent.
    
    logger_base_dir = os.environ.get("LOGGER_BASE_DIR")
    if not logger_base_dir:
        log.critical("You must set the LOGGER_BASE_DIR environment variable")
        sys.exit(1)
    
    logger_base_dir = os.path.realpath(logger_base_dir)
    
    restart_command = ("nohup " + logger_base_dir + 
                       "/rfm_ecomanager_logger/rfm_ecomanager_logger/"
                       "rfm_ecomanager_logger.py")
    
    manager.append(Process(name="rfm_ecomanager_logger.py",
                        restart_command=restart_command))
    
    # Check if /flac directory exists.  If it does then snd_card_power_meter
    # is assumed to be installed.
    scpm_is_installed = os.path.exists('/flac')
    if scpm_is_installed:
        manager.append(Process(name='record.py', 
                               restart_command='nohup ' + logger_base_dir +
                               '/snd_card_power_meter/scripts/record.py'))
        
        scpm_dat_dir = base_data_dir + '/high-freq-mains'
        scpm_datfiles = os.listdir(scpm_dat_dir)
        scpm_datfiles = [f for f in scpm_datfiles 
                         if f.startswith('mains-') and f.endswith('.dat')]
        scpm_datfiles.sort()
        last_datfile = os.path.join(scpm_dat_dir, scpm_datfiles[-1])
        manager.append(File(last_datfile))

    ########### FILEGROWS ###############################################
    # manager.append(FileGrows("cron.log"))

    ########### COMMANDS TO RUN WHENEVER STATE CHANGES ##################
    rfm_ecomanager_logger_log_cmd = ("tail -n 50 " + logger_base_dir +
                      "/rfm_ecomanager_logger/rfm_ecomanager_logger.log",
                      True) # second argument switches output of stdout
    
    manager.state_change_cmds.append(rfm_ecomanager_logger_log_cmd)
    
    ########### COMMANDS TO RUN AT SHUTDOWN ############################
    manager.shutdown_cmds.append(
                 ("tail -n 50 " + os.path.join(FILE_PATH, "babysitter.log"),
                  True))

    ########### LOAD POWER DATA ########################################
    data_dir = manager.load_powerdata(directory=base_data_dir,
                                      numeric_subdirs=True,
                                      timeout=500)
    
    ########### HEARTBEAT ###############################################
    manager.heartbeat.hour = 6 # Hour of each day to send heartbeat (24hr clock)
        
    manager.heartbeat.cmds.append(rfm_ecomanager_logger_log_cmd)
    
    if scpm_is_installed:
        manager.heartbeat.cmds.append(('tail -n 75 ' + logger_base_dir + 
                                       '/snd_card_power_meter/scpm.log', True))
    
    rsync_cron = logger_base_dir + "/rsync/rsync_cron.log" 
    manager.heartbeat.cmds.append(("tail -n 75 " + rsync_cron, True))
    manager.heartbeat.cmds.append(("date -r " + rsync_cron, True))
    
    cron = logger_base_dir + "/rfm_ecomanager_logger/cron.log" 
    manager.heartbeat.cmds.append(("tail " + cron, True))
    manager.heartbeat.cmds.append(("date -r " + cron, True))
     
    # Manually provide the --data-dir (instead of allowing powerstats 
    # to work this out for itself) so we guarantee that powerstats
    # will always produce data for the same data dir that babysitter
    # is looking at.  Especially important if babysitter spots a new data
    # subdir whilst running.
    manager.heartbeat.cmds.append((logger_base_dir +
                      "/powerstats/powerstats/powerstats.py"
                      " --data-dir " + data_dir + " --html --cache"
                      " --high-freq-data-dir " + base_data_dir + "/high-freq-mains",
                      True)) # second argument switches output of stdout
    
    manager.heartbeat.html_file = (data_dir + "/html/index.html")
    

def main():
    init_logger()
    log.debug('\nMAIN: babysitter.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    # Load Manager in a loop so we can reload Manager
    # if we get a NewDataDirError.
    MAX_RETRIES = 5
    retries = 0
    previous_loop_time = time.time()
    RESET_AFTER = 60*60 # reset retries after this number of seconds
    
    while retries < MAX_RETRIES:
        if time.time() > previous_loop_time + RESET_AFTER:
            retries = 0
        else:
            retries += 1
            
        manager = Manager()
        _set_config(manager)

        try:
            previous_loop_time = time.time()            
            manager.run()
        except KeyboardInterrupt:
            # Catch this so we don't spit out unwanted errors in the log
            manager.shutdown_reason = "KeyboardInterrupt or SIGINT signal received"
            log.info(manager.shutdown_reason)
            manager.shutdown()
            break
        except NewDataDirError:
            log.info("New data directory found. Re-starting babysitter.")
        except Exception, e:
            log.exception("")
            manager.shutdown_reason = "EXCEPTION: " + str(e)
            log.info(manager.shutdown_reason)
            manager.shutdown()
            raise
        else:
            manager.shutdown()
            break


if __name__ == "__main__":
    main()
