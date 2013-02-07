#! /usr/bin/python
from __future__ import print_function, division
import logging
log = logging.getLogger("babysitter")
import os
from babysitter import Manager, DiskSpaceRemaining, Process, FileGrows, File
import time
import sys
import email_config

"""
This script takes advantage of the babysitter package.
This script is both an example of how to use babysitter
and also babysits rfm_ecomanager_logger.

REQUIREMENTS
============

EMAIL CONFIG
------------

Create an email_config.py file with the following text:
SMTP_SERVER = ""
EMAIL_FROM  = ""
EMAIL_TO    = ""
USERNAME    = ""
PASSWORD    = ""

ENVIRONMENT VARIABLES
---------------------

The following environment variables must be set:
 - DATA_DIR
 - LOGGER_BASE_DIR

"""

def init_logger():
    # create logger
    logger = logging.getLogger("babysitter")
    logger.setLevel(logging.DEBUG)

    # date formatting
    datefmt = "%y-%m-%d %H:%M:%S"

    # create console handler (ch) for stdout
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch_formatter = logging.Formatter('%(asctime)s %(levelname)s '
                        '%(message)s', datefmt=datefmt)
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)
    
    # create file handler (fh) for babysitter.log
    logfile = os.path.dirname(os.path.realpath(__file__)) + "/babysitter.log"
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

    ########### DISK SPACE CHECKER ######################################
    manager.append(DiskSpaceRemaining(threshold=200, path="/"))

    ########### FILES ###################################################
    # manager.append(File(name="/path/to/file", timeout=120))
        
    ########### POWERDATA ###############################################
    data_dir = os.environ.get("DATA_DIR")
    if not data_dir:
        log.critical("You must set the DATA_DIR environment variable")
        sys.exit(1)
        
    data_dir = manager.load_powerdata(directory=data_dir,
                                      numeric_subdirs=True,
                                      timeout=300)

    ########### PROCESSES ###############################################
    
    logger_base_dir = os.environ.get("LOGGER_BASE_DIR")
    if not logger_base_dir:
        log.critical("You must set the LOGGER_BASE_DIR environment variable")
        sys.exit(1)
    
    logger_base_dir = os.path.realpath(logger_base_dir)
    
    restart_command = ("nohup " + logger_base_dir + 
                       "/rfm_ecomanager_logger/rfm_ecomanager_logger/rfm_ecomanager_logger.py")
    
    manager.append(Process(name="rfm_ecomanager_logger.py",
                        restart_command=restart_command))

    ########### FILEGROWS ###############################################
    # manager.append(FileGrows("cron.log"))
    
    ########### HEARTBEAT ###############################################
    manager.heartbeat.hour = 6 # 24hr clock
    manager.heartbeat.cmd.append(("tail -n 50 " + logger_base_dir +
                                  "/rfm_ecomanager_logger/rfm_ecomanager_logger.log",
                                  True))    
    manager.heartbeat.cmd.append((logger_base_dir +
                          "/powerstats/powerstats/powerstats.py --html --cache", False))
    
    manager.heartbeat.html_file = (data_dir + "/html/index.html")
    

def main():
    init_logger()
    log.debug('MAIN: babysitter.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    # register SIGINT and SIGTERM handler
    log.info("MAIN: setting signal handlers")

    manager = Manager()
    _set_config(manager)
    
    WAIT = 60 # seconds
    log.info("Waiting {} seconds for data files to become available...".format(WAIT))
    time.sleep(WAIT)
    log.info("...done waiting.  Now starting manager.run()")
    
    try:
        manager.run()
    except KeyboardInterrupt:
        # Catch this so we don't spit out unwanted errors in the log
        pass

if __name__ == "__main__":
    main()
