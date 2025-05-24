from utils.logger import get_logger
logger = get_logger(__name__)
from apscheduler.schedulers.background import BackgroundScheduler

def start_schedular():

    scheduler = BackgroundScheduler()
    scheduler.start()
    logger.info("Scheduler gestart.")

