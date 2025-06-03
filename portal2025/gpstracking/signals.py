from django.db import connection
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from utils.logger import get_logger
from gpstracking.util_db import GpsTrackingUtilDB

from .models import Tracker, TrackerGroup, TrackerIdentifier, TrackerIdentifierType




logger = get_logger(__name__)

@receiver(post_save, sender=TrackerGroup)
def create_or_update_sql_view(sender, instance: TrackerGroup, **kwargs):
    sql_main, sql_track, view_name = GpsTrackingUtilDB.generate_tracker_view_sql(instance)

    if not sql_main or not sql_track:
        logger.warning(f"Views niet aangemaakt voor groep '{instance.smartcode}' (mogelijk geen velden geselecteerd).")
        return

    sql_drop_main = f"DROP VIEW IF EXISTS {view_name};"
    sql_drop_track = f"DROP VIEW IF EXISTS {view_name}_tracks;"

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql_drop_main)
            cursor.execute(sql_main)
            cursor.execute(sql_drop_track)
            cursor.execute(sql_track)
        logger.info(f"SQL views aangemaakt of bijgewerkt voor groep '{instance.smartcode}'")
    except Exception as e:
        logger.error(f"Fout bij aanmaken van views voor '{view_name}': {e}")



@receiver(post_delete, sender=TrackerGroup)
def drop_sql_view_on_delete(sender, instance: TrackerGroup, **kwargs):
    view_name = f"v_tracker_group_{instance.smartcode}".lower()
    sql = f"""DROP VIEW IF EXISTS {view_name};        
              DROP VIEW IF EXISTS {view_name}_tracks;"""
    with connection.cursor() as cursor:
        cursor.execute(sql)


@receiver(m2m_changed, sender=Tracker.groups.through)
def ensure_identifier_type_groups_present(sender, instance, action, **kwargs):
    if action in ['post_remove', 'post_clear', 'post_add']:
        current_group_ids = set(instance.groups.values_list('id', flat=True))
        expected_group_ids = set(
            TrackerGroup.objects.filter(
                identifier_types__in=instance.identifiers.values_list('identifier_type', flat=True)
            ).values_list('id', flat=True)
        )
        missing_group_ids = expected_group_ids - current_group_ids
        if missing_group_ids:
            instance.groups.add(*missing_group_ids)


@receiver(post_delete, sender=TrackerIdentifier)
def remove_groups_on_identifier_delete(sender, instance, **kwargs):
    tracker = instance.tracker
    for group in instance.identifier_type.groups.all():
        other_ids = tracker.identifiers.filter(identifier_type__groups=group).exclude(pk=instance.pk)
        if not other_ids.exists():
            tracker.groups.remove(group)


@receiver(m2m_changed, sender=TrackerGroup.identifier_types.through)
def sync_trackers_on_identifiertype_change(sender, instance, action, pk_set, **kwargs):
    if action == 'post_add':
        identifiers = TrackerIdentifier.objects.filter(identifier_type_id__in=pk_set)
        trackers = Tracker.objects.filter(identifiers__in=identifiers).distinct()
        for tracker in trackers:
            tracker.groups.add(instance)

    elif action == 'post_remove':
        removed_type_ids = pk_set
        trackers = Tracker.objects.filter(groups=instance).distinct()

        for tracker in trackers:
            tracker_type_ids = set(tracker.identifiers.values_list('identifier_type_id', flat=True))
            still_valid_type_ids = set(instance.identifier_types.values_list('code', flat=True))

            if not tracker_type_ids.intersection(still_valid_type_ids):
                had_removed_type = tracker.identifiers.filter(identifier_type_id__in=removed_type_ids).exists()
                if had_removed_type:
                    tracker.groups.remove(instance)