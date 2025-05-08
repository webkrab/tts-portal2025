# models.py
import hashlib
import json
import uuid

from django.contrib.gis.db import models as gis_models
from django.db import models
from django.db.models.signals import post_delete, m2m_changed
from django.dispatch import receiver
from django.core.validators import MinValueValidator, MaxValueValidator
from datetime import datetime, timezone


def get_tracker_field_choices():
    return [
        (field.name, field.name)
        for field in Tracker._meta.get_fields()
        if isinstance(field, models.Field) and field.concrete and not field.auto_created
    ]


class TrackerIdentifierType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.name}"


class TrackerGroup(models.Model):
    name = models.CharField(max_length=255, unique=True)
    area = gis_models.MultiPolygonField(geography=True, blank=True, null=True, srid=4326)
    visible_fields = models.JSONField(default=list, blank=True)

    identifier_types = models.ManyToManyField(
        TrackerIdentifierType,
        blank=True,
        related_name='groups'
    )

    def __str__(self):
        return f'{self.pk} | {self.name}'


class Tracker(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=True)
    screen_name = models.CharField(max_length=255)
    icon = models.CharField(max_length=255)

    ais_type = models.CharField(max_length=255, blank=True, null=True)
    ais_name = models.CharField(max_length=255, blank=True, null=True)
    ais_callsign = models.CharField(max_length=255, blank=True, null=True)
    ais_length = models.DecimalField(max_digits=5, decimal_places=2,
                                     validators=[MinValueValidator(0), MaxValueValidator(500)],
                                     default=0, blank=True, null=True)
    ais_width = models.DecimalField(max_digits=5, decimal_places=2,
                                    validators=[MinValueValidator(0), MaxValueValidator(500)],
                                    default=0, blank=True, null=True)

    adsb_type = models.CharField(max_length=255, blank=True, null=True)
    adsb_registration = models.CharField(max_length=255, blank=True, null=True)
    adsb_callsign = models.CharField(max_length=255, blank=True, null=True)

    altitude = models.FloatField(default=0, blank=True, null=True)
    speed = models.FloatField(blank=True, null=True)
    heading = models.FloatField(blank=True, null=True)
    position_timestamp = models.BigIntegerField(blank=True, null=True, help_text="UNIX tijd in ms")
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)

    groups = models.ManyToManyField(TrackerGroup, related_name='trackers', blank=True)

    @property
    def position_timestamp_display(self):
        if self.position_timestamp:
            dt = datetime.fromtimestamp(self.position_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    def __str__(self):
        return self.screen_name

@receiver(m2m_changed, sender=Tracker.groups.through)
def ensure_identifier_type_groups_present(sender, instance, action, **kwargs):
    if action in ['post_remove', 'post_clear', 'post_add']:
        # Huidige groepen van de tracker
        current_group_ids = set(instance.groups.values_list('id', flat=True))

        # Groepen die deze tracker zou moeten hebben op basis van identifiers
        expected_group_ids = set(
            TrackerGroup.objects.filter(
                identifier_types__in=instance.identifiers.values_list('identifier_type', flat=True)
            ).values_list('id', flat=True)
        )

        missing_group_ids = expected_group_ids - current_group_ids

        if missing_group_ids:
            instance.groups.add(*missing_group_ids)

class TrackerIdentifier(models.Model):
    external_id = models.CharField(max_length=255)
    identifier_type = models.ForeignKey(TrackerIdentifierType, on_delete=models.PROTECT, related_name='tracker_identifiers')
    tracker = models.ForeignKey(Tracker, on_delete=models.CASCADE, related_name='identifiers')
    identkey = models.CharField(max_length=255, unique=True, editable=False)

    def save(self, *args, **kwargs):
        self.identkey = f"{self.identifier_type.name}_{self.external_id}".upper()
        super().save(*args, **kwargs)
        if self.identifier_type:
            self.tracker.groups.add(*self.identifier_type.groups.all())

    def __str__(self):
        return f"{self.identifier_type.name}: {self.external_id} | {self.tracker.screen_name}"


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
        removed_type_ids = pk_set  # Verwijderde identifier_type IDs
        trackers = Tracker.objects.filter(groups=instance).distinct()

        for tracker in trackers:
            # Alle identifier types van deze tracker
            tracker_type_ids = set(tracker.identifiers.values_list('identifier_type_id', flat=True))

            # Zijn er types over die nog aan deze groep gekoppeld zijn?
            still_valid_type_ids = set(
                    instance.identifier_types.values_list('id', flat=True)
            )

            # Is de tracker nog steeds geldig lid van de groep?
            if not tracker_type_ids.intersection(still_valid_type_ids):
                # Check of hij enkel in de groep zat vanwege types die nu verwijderd zijn
                had_removed_type = tracker.identifiers.filter(
                        identifier_type_id__in=removed_type_ids
                ).exists()

                if had_removed_type:
                    tracker.groups.remove(instance)


class TrackerMessage(models.Model):
    tracker_identifier = models.ForeignKey(TrackerIdentifier, on_delete=models.CASCADE, related_name='messages')
    msgtype = models.CharField(max_length=10, default=None)
    content = models.JSONField()
    created_at = models.BigIntegerField(help_text="UNIX tijd in milliseconden (UTC)")
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    sha256_key = models.CharField(max_length=64, blank=True, null=True, unique=True)

    @property
    def created_at_display(self):
        if self.created_at:
            dt = datetime.fromtimestamp(self.created_at / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    def save(self, *args, **kwargs):
        if not self.sha256_key and self.content:
            base_str = json.dumps(self.content, sort_keys=True)
            self.sha256_key = hashlib.sha256(base_str.encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Message for {self.tracker_identifier} at {self.created_at}"
