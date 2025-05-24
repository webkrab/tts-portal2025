from apscheduler.schedulers.background import BackgroundScheduler
from taskschedular.models import ScheduledJob, JobHistory
from django.utils import timezone
from utils.logger import get_logger


logger = get_logger(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

def start(job):
    if 1 == 2:
        from taskschedular.jobs import job_registry
        functie = job_registry.get(job.functie)
        if functie:
            scheduler.add_job(
                functie,
                trigger=job.trigger_type,
                id=str(job.id),
                kwargs=job.parameters or {},
                **job.trigger_args
            )
            job.status = 'actief'
            job.save(update_fields=['status'])
            logger.info("Job gestart", extra={"info": f"[id={job.id}, naam={job.naam}]"})

def stop(job):
    scheduler.remove_job(str(job.id))
    job.status = 'gestopt'
    job.save(update_fields=['status'])
    logger.info("Job gestopt", extra={"info": f"[id={job.id}, naam={job.naam}]"})

def restart(job):
    stop(job)
    start(job)

def reload_jobs():
    scheduler.remove_all_jobs()
    for job in ScheduledJob.objects.filter(actief=True):
        start(job)

scheduler_controller = {
    "start": start,
    "stop": stop,
    "restart": restart,
    "reload_jobs": reload_jobs,
}
