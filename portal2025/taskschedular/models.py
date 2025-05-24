from django.db import models

class ScheduledJob(models.Model):
    naam = models.CharField(max_length=100)
    functie = models.CharField(max_length=200)
    parameters = models.JSONField(blank=True, null=True)
    trigger_type = models.CharField(max_length=20, choices=[('interval', 'Interval'), ('cron', 'Cron')])
    trigger_args = models.JSONField(help_text="Bijv: {'seconds': 30} of {'hour': 2}")
    actief = models.BooleanField(default=True)
    status = models.CharField(max_length=20, default="gestopt")
    info = models.TextField(blank=True)

    def __str__(self):
        return f"[{self.id}] {self.naam}"

class JobHistory(models.Model):
    job = models.ForeignKey(ScheduledJob, on_delete=models.CASCADE)
    gestart_op = models.DateTimeField(auto_now_add=True)
    voltooid_op = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20)
    foutmelding = models.TextField(blank=True)
    resultaat = models.TextField(blank=True)
    info = models.TextField(blank=True)