from django.db import models
from django.db.models import Case, When, Value, IntegerField

class CityQuerySet(models.QuerySet):
    def ordered_by_land_then_name(self):
        return self.annotate(
            land_prioriteit=Case(
                When(landcode='NL', then=Value(1)),
                When(landcode='BE', then=Value(2)),
                When(landcode='DE', then=Value(3)),
                default=Value(4),
                output_field=IntegerField(),
            )
        ).order_by('land_prioriteit', 'wps_naam')

class City(models.Model):
    landcode = models.CharField(max_length=2, default='NL')
    wps_code = models.CharField(max_length=6, null=True, blank=True)
    wps_naam = models.CharField(max_length=28, null=True, blank=True)
    wps_abv = models.CharField(max_length=6, null=True, blank=True, db_index=True)
    municipality_code = models.CharField(max_length=6, null=True, blank=True)
    municipality = models.CharField(max_length=29, null=True, blank=True)
    municipality_1 = models.CharField(max_length=29, null=True, blank=True)
    municipality_abv = models.CharField(max_length=9, null=True, blank=True)
    state_code = models.CharField(max_length=4, null=True, blank=True)
    state = models.CharField(max_length=13, null=True, blank=True)
    landsdeel_code = models.CharField(max_length=4, null=True, blank=True)
    landsdeel = models.CharField(max_length=15, null=True, blank=True)
    veiligheidsregio_code = models.CharField(max_length=2, null=True, blank=True)
    veiligheidsregio = models.CharField(max_length=25, null=True, blank=True)
    veiligheidsregio_num = models.IntegerField(null=True, blank=True)
    mk_naam = models.CharField(max_length=20, null=True, blank=True)
    mk_plaats = models.CharField(max_length=14, null=True, blank=True)

    objects = CityQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=['wps_abv'], name='idx_city_wps_abv')]
        unique_together = (('landcode', 'wps_code'),)
        ordering = ['wps_naam']  # fallback

    def save(self, *args, **kwargs):
        for field in self._meta.get_fields():
            if isinstance(field, models.CharField):
                val = getattr(self, field.name)
                if isinstance(val, str):
                    setattr(self, field.name, val.strip())
        super().save(*args, **kwargs)

    @property
    def shortname(self):
        return f"{self.landcode}-{self.wps_abv}" if self.wps_abv else None

    def __str__(self):
        if self.wps_naam:
            return f"{self.wps_naam}, {self.state}, {self.landcode}"
        return f"{self.landcode}, {self.wps_code}"

class AppliedDataFile(models.Model):
    """
    Houdt bij welke JSON-datafiles al ingelezen zijn via load_dataloads.
    """
    filename = models.CharField(
        max_length=255,
        unique=True,
        help_text="Naam van het JSON-bestand (bijv. 'initial_city.json')"
    )
    applied_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp waarop dit bestand is ingeladen"
    )

    def __str__(self):
        return self.filename