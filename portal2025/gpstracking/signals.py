from django.db import connection
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from utils.logger import get_logger

from .models import Tracker, TrackerGroup, TrackerIdentifier, TrackerIdentifierType




logger = get_logger(__name__)


@receiver(post_save, sender=TrackerGroup)
def create_or_update_sql_view(sender, instance: TrackerGroup, **kwargs):
    view_name = f"v_tracker_group_{instance.smartcode}".lower()
    fields = instance.visible_fields

    if not fields:
        return

    valid_fields = {f.name for f in Tracker._meta.fields if f.concrete}

    view_columns = []
    select_parts = []

    for field in fields:
        if field == 'ais_dimensions':
            select_parts.append(
                "COALESCE(tracker.ais_length::text || 'm', '?') || ' x ' || COALESCE(tracker.ais_width::text || 'm', '?') AS ais_dimensions"
            )
            view_columns.append('ais_dimensions')
        elif field == 'age_in_sec':
            select_parts.append(
                "FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 1000) AS age_in_sec"
            )
            view_columns.append('age_in_sec')
        elif field == 'age_human':
            select_parts.append("""
                TRIM(BOTH ' ' FROM
                    CONCAT_WS(' ',
                        CASE WHEN FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 86400000) > 0 THEN
                            FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 86400000)::int || 'd'
                        END,
                        CASE WHEN MOD(FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 3600000), 24) > 0 THEN
                            MOD(FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 3600000), 24)::int || 'h'
                        END,
                        CASE WHEN MOD(FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 60000), 60) > 0 THEN
                            MOD(FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 60000), 60)::int || 'm'
                        END,
                        CASE WHEN MOD(FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 1000), 60) > 0 THEN
                            MOD(FLOOR((EXTRACT(EPOCH FROM now()) * 1000 - tracker.position_timestamp) / 1000), 60)::int || 's'
                        END
                    )
                ) AS age_human
            """)
            view_columns.append('age_display')

        elif field == 'display_name':
            select_parts.append("""
                    COALESCE(
                        tracker.custom_name,
                        (
                            SELECT STRING_AGG(ti.identkey, ' | ')
                            FROM gpstracking_trackeridentifier ti
                            WHERE ti.tracker_id = tracker.id
                        ),
                        tracker.id::text
                    ) AS display_name
                """)
            view_columns.append('display_name')

        elif field in valid_fields:
            select_parts.append(f"tracker.{field}")
            view_columns.append(field)
        else:
            logger.warning(f"'{field}' is not a valid field of Tracker and was skipped.")

    if not select_parts:
        logger.warning(f"No valid fields to generate SQL view for group {instance.smartcode}")
        return

    select_clause = ', '.join(select_parts)
    column_clause = ', '.join(view_columns)

    where_clause = f"tg.trackergroup_id = {instance.pk}"

    if instance.area:
        ewkt = instance.area.ewkt
        geom_filter = f"ST_Within(tracker.position::geometry, ST_GeomFromEWKT('{ewkt}'))"
        where_clause += f" AND {geom_filter}"

    sql_drop = f"DROP VIEW IF EXISTS {view_name};"
    sql_create = f"""
    CREATE VIEW {view_name} ({column_clause}) AS
    SELECT {select_clause}
    FROM {Tracker._meta.db_table} AS tracker
    INNER JOIN {Tracker.groups.through._meta.db_table} AS tg
        ON tracker.id = tg.tracker_id
    WHERE {where_clause};
    """

    logger.debug(sql_create)
    with connection.cursor() as cursor:
        cursor.execute(sql_drop)
        cursor.execute(sql_create)


@receiver(post_delete, sender=TrackerGroup)
def drop_sql_view_on_delete(sender, instance: TrackerGroup, **kwargs):
    view_name = f"v_tracker_group_{instance.smartcode}".lower()
    sql = f"DROP VIEW IF EXISTS {view_name};"
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