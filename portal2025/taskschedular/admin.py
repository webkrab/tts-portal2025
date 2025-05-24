from django.contrib import admin
from .models import ScheduledJob, JobHistory
from .scheduler import scheduler_controller

@admin.register(ScheduledJob)
class ScheduledJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'naam', 'functie', 'actief', 'status')
    list_filter = ('actief', 'status')
    actions = ['start_job', 'stop_job', 'enable_job', 'disable_job', 'restart_job']

    def start_job(self, request, queryset):
        for job in queryset:
            scheduler_controller['start'](job)
        self.message_user(request, f"{queryset.count()} job(s) gestart.")

    def stop_job(self, request, queryset):
        for job in queryset:
            scheduler_controller['stop'](job)
        self.message_user(request, f"{queryset.count()} job(s) gestopt.")

    def restart_job(self, request, queryset):
        for job in queryset:
            scheduler_controller['restart'](job)
        self.message_user(request, f"{queryset.count()} job(s) herstart.")

    def enable_job(self, request, queryset):
        queryset.update(actief=True)
        self.message_user(request, "Job(s) geactiveerd.")
        scheduler_controller['reload_jobs']()

    def disable_job(self, request, queryset):
        queryset.update(actief=False)
        self.message_user(request, "Job(s) gedeactiveerd.")
        scheduler_controller['reload_jobs']()

@admin.register(JobHistory)
class JobHistoryAdmin(admin.ModelAdmin):
    list_display = ('job', 'gestart_op', 'voltooid_op', 'status')
    list_filter = ('status', 'job__naam')
    search_fields = ('job__naam', 'resultaat', 'foutmelding')