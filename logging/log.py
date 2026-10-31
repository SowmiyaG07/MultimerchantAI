import logging
import os
from logging.handlers import RotatingFileHandler


class Applogger:
    """Sets up logging once for the whole application."""
    
    
    LOG_DIR = "logs"
    LOG_FILE = os.path.join(LOG_DIR,"app.log")
    LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    
    MAX_BYTES = 10*1024 * 1024
    BACKUP_COUNT = 5 
    
    @classmethod
    def setup(cls):
        """Call this one time, when the app starts."""
        os.makedirs(cls.LOG_DIR,exist_ok=True)
        console_handler = logging.StreamHandler()
        file_handler = RotatingFileHandler(cls.LOG_FILE,maxBytes = cls.MAX_BYTES,backupCount = cls.BACKUP_COUNT,encoding= "utf-8")
        logging.basicConfig(level=logging.INFO,format=cls.LOG_FORMAT,handlers=[console_handler,file_handler])
        
    @staticmethod
    def get_loggerdata(name):
        
        """Get a logger for one file. Pass __name__ as the name."""
        return logging.getLogger(name)
    
    