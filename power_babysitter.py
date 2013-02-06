#! /usr/bin/python
from __future__ import print_function, division
import logging
log = logging.getLogger("babysitter")
import os
from babysitter import Manager, DiskSpaceRemaining, Process, FileGrows, File
import datetime
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
 - RFM_ECOMANAGER_LOGGER_DIR
 - POWERSTATS_DIR

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

def _shutdown():
    log.info("Shutting down.")
    logging.shutdown() 


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
        
    manager.load_powerdata(directory=data_dir,
                         numeric_subdirs=True,
                         timeout=300)

    ########### PROCESSES ###############################################
    
    rfm_ecomanager_logger_dir = os.environ.get("RFM_ECOMANAGER_LOGGER_DIR")
    if not rfm_ecomanager_logger_dir:
        log.critical("You must set the RFM_ECOMANAGER_LOGGER_DIR environment variable")
        sys.exit(1)
    
    restart_command = ("nohup " +
                       os.path.realpath(rfm_ecomanager_logger_dir) + 
                       "/rfm_ecomanager_logger/rfm_ecomanager_logger.py")
    
    manager.append(Process(name="rfm_ecomanager_logger.py",
                        restart_command=restart_command))

    ########### FILEGROWS ###############################################
    # manager.append(FileGrows("cron.log"))
    
    ########### HEARTBEAT ###############################################
    powerstats_dir = os.path.realpath(os.environ.get("POWERSTATS_DIR"))
    manager.heartbeat.hour = 6 # 24hr clock
    manager.heartbeat.cmd = (powerstats_dir +
                          "/powerstats/powerstats.py --html --cache")
    manager.heartbeat.html_file = (powerstats_dir + "/html/index.html")
    

def main():
    init_logger()
    log.debug('MAIN: babysitter.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    # register SIGINT and SIGTERM handler
    log.info("MAIN: setting signal handlers")

    # Wrap manager.run() in a "try... except" block so we
    # can gracefully catch KeyboardInterrupt exceptions or we
    # can send any unexpected exceptions to logger.
    try:
        manager = Manager()
        _set_config(manager)
        manager.run()
    except KeyboardInterrupt:
        manager.shutdown()
    except SystemExit, e:
        log.error(e)
    except:
        log.exception("")
    
    _shutdown()

if __name__ == "__main__":
    main()
