import hashlib
import json
import uuid

from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Point
from django.db import models, connection
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.validators import MinValueValidator, MaxValueValidator
from django.contrib.gis.db.models import GeometryField  # 👈 belangrijk
from django.contrib import messages

def get_tracker_field_choices():
    """
    Geeft een lijst van veldnamen uit het Tracker-model, inclusief geometryvelden.

    Returns:
        list: Lijst met tuples (veldnaam, veldnaam) geschikt voor use in keuzelijsten.
    """
    return [
        (field.name, field.name)
        for field in Tracker._meta.get_fields()
        if isinstance(field, (models.Field, GeometryField)) and field.concrete and not field.auto_created
    ]

class TrackerIdentifierType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} - {self.description or ''}".strip()


class TrackerGroup(models.Model):
    name = models.CharField(max_length=255, unique=True)
    area = gis_models.MultiPolygonField(geography=True, blank=True, null=True, srid=4326)
    visible_fields = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f'{self.pk} | {self.name}'


class Tracker(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=True)
    screen_name = models.CharField(max_length=255)
    icon = models.CharField(max_length=255)

    # AIS velden
    ais_type = models.CharField(max_length=255, blank=True, null=True)
    ais_name = models.CharField(max_length=255, blank=True, null=True)
    ais_callsign = models.CharField(max_length=255, blank=True, null=True)
    ais_length = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(500)],
        default=0, blank=True, null=True
    )
    ais_width = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(500)],
        default=0, blank=True, null=True
    )

    # ADS-B velden
    adsb_type = models.CharField(max_length=255, blank=True, null=True)
    adsb_registration = models.CharField(max_length=255, blank=True, null=True)
    adsb_callsign = models.CharField(max_length=255, blank=True, null=True)

    # Positie
    latitude = models.DecimalField(
        max_digits=9, decimal_places=7,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        default=0, blank=True, null=True
    )
    longitude = models.DecimalField(
        max_digits=10, decimal_places=7,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
        default=0, blank=True, null=True
    )
    altitude = models.FloatField(default=0, blank=True, null=True)
    speed = models.FloatField(blank=True, null=True)
    heading = models.FloatField(blank=True, null=True)
    position_timestamp = models.BigIntegerField(
        blank=True, null=True, help_text="UNIX tijd in milliseconden (UTC)"
    )
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)

    groups = models.ManyToManyField(TrackerGroup, related_name='trackers', blank=True)

    @property
    def ais_dimensions(self):
        """
        Samengestelde weergave van AIS lengte en breedte in meters.

        Returns:
            str: Formaat 'Lengte x Breedte' zoals '123.4m x 23.1m', of '-' als niet beschikbaar.
        """
        if self.ais_length and self.ais_width:
            return f"{self.ais_length}m x {self.ais_width}m"
        elif self.ais_length:
            return f"{self.ais_length}m x ?"
        elif self.ais_width:
            return f"? x {self.ais_width}m"
        return "-"

    def save(self, *args, **kwargs):
        if self.position:
            # Haal lat/lon/altitude uit geometrie
            self.longitude = self.position.x
            self.latitude = self.position.y
            # Alleen als er een Z-dimensie is
            if self.position.has_z:
                self.altitude = self.position.z
        elif self.latitude is not None and self.longitude is not None:
            # Maak een nieuwe geometrie op basis van velden
            coords = [float(self.longitude), float(self.latitude)]
            self.position = Point(*coords)
        else:
            self.position = None

        super().save(*args, **kwargs)
    def __str__(self):
        return self.screen_name


class TrackerIdentifier(models.Model):
    external_id = models.CharField(max_length=255)
    identifier_type = models.ForeignKey(
        TrackerIdentifierType,
        on_delete=models.PROTECT,
        related_name='tracker_identifiers'
    )
    tracker = models.ForeignKey(Tracker, on_delete=models.CASCADE, related_name='identifiers')
    identkey = models.CharField(max_length=255, unique=True, editable=False)

    def save(self, *args, **kwargs):
        type_str = self.identifier_type.name if self.identifier_type else "UNKNOWN"
        self.identkey = f"{type_str}_{self.external_id}".upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.identifier_type.name}: {self.external_id}"


class Message(models.Model):
    tracker_identifier = models.ForeignKey(TrackerIdentifier, on_delete=models.CASCADE, related_name='messages')
    content = models.JSONField()
    created_at = models.BigIntegerField(help_text="UNIX tijd in milliseconden (UTC)")
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    sha256_key = models.CharField(max_length=64, blank=True, null=True, unique=True)

    def save(self, *args, **kwargs):
        if not self.sha256_key and self.content:
            base_str = json.dumps(self.content, sort_keys=True)
            self.sha256_key = hashlib.sha256(base_str.encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Message for {self.tracker_identifier} at {self.created_at}"


@receiver(post_save, sender=TrackerGroup)
def create_or_update_sql_view(sender, instance: TrackerGroup, **kwargs):
    group_id = instance.id
    view_name = f"view_tracker_group_{group_id}".lower()
    fields = instance.visible_fields

    if not fields:
        return

    view_columns = ['id']
    select_parts = ['tracker.id']

    for field in fields:
        if field == 'ais_dimensions':
            select_parts.append(
                "COALESCE(tracker.ais_length::text || 'm', '?') || ' x ' || COALESCE(tracker.ais_width::text || 'm', '?') AS ais_dimensions"
            )
            view_columns.append('ais_dimensions')
        else:
            select_parts.append(f"tracker.{field}")
            view_columns.append(field)

    select_clause = ', '.join(select_parts)
    column_clause = ', '.join(view_columns)

    # Basisfilter op group-koppeling
    where_clause = f"tg.trackergroup_id = '{group_id}'"

    # Extra geometrische filter, met expliciete typecasting naar geometry
    if instance.area:
        ewkt = instance.area.ewkt  # Bijvoorbeeld: SRID=4326;MULTIPOLYGON(...)
        geom_filter = f"ST_Within(tracker.position::geometry, ST_GeomFromEWKT('{ewkt}'))"
        where_clause = f"{where_clause} AND {geom_filter}"

    sql_drop = f"DROP VIEW IF EXISTS {view_name};"
    sql_create = f"""
    CREATE VIEW {view_name} ({column_clause}) AS
    SELECT {select_clause}
    FROM {Tracker._meta.db_table} AS tracker
    INNER JOIN {Tracker.groups.through._meta.db_table} AS tg
        ON tracker.id = tg.tracker_id
    WHERE {where_clause};
    """
    print(sql_create)
    with connection.cursor() as cursor:
        cursor.execute(sql_drop)
        cursor.execute(sql_create)


@receiver(post_delete, sender=TrackerGroup)
def drop_sql_view_on_delete(sender, instance: TrackerGroup, **kwargs):
    view_name = f"view_tracker_group_{instance.id}".lower()
    sql = f"DROP VIEW IF EXISTS {view_name};"
    with connection.cursor() as cursor:
        cursor.execute(sql)