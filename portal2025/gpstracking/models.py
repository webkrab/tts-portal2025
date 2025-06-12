import time
import uuid
from datetime import datetime, timedelta, timezone

from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import UniqueConstraint
from django.utils.functional import lazy
from django.utils.translation import gettext_lazy as _
from utils.models import City


def get_alarm_choises():
    ALARM_CHOICES = [
            ('general', 'Alarm'),
            ('sos', 'SOS'),
            ('vibration', 'Trilling'),
            ('movement', 'Beweging'),
            ('lowspeed', 'Lage snelheid'),
            ('overspeed', 'Snelheidsoverschreiding'),
            ('fallDown', 'Gevallen'),
            ('lowPower', 'Lage stroom'),
            ('lowBattery', 'Laag accuniveau'),
            ('fault', 'Fout'),
            ('powerOff', 'Uitgeschakeld'),
            ('powerOn', 'Ingeschakeld'),
            ('door', 'Deur'),
            ('lock', 'Afgesloten'),
            ('unlock', 'Geopend'),
            ('geofence', 'Geofence'),
            ('geofenceEnter', 'Geofence binnengegaan'),
            ('geofenceExit', 'Geofence verlaten'),
            ('gpsAntennaCut', 'GPS antenne verbroken'),
            ('accident', 'Ongeluk'),
            ('tow', 'Gesleept'),
            ('idle', 'Rust'),
            ('highRpm', 'Hoge RPM'),
            ('hardAcceleration', 'Harde versnelling'),
            ('hardBraking', 'Harde remming'),
            ('hardCornering', 'Scherpe bocht'),
            ('laneChange', 'Rijbaanwisseling'),
            ('fatigueDriving', 'Vermoeid rijden'),
            ('powerCut', 'Stroomonderbreking'),
            ('powerRestored', 'Stroom hersteld'),
            ('jamming', 'Signaal verstoring'),
            ('temperature', 'Temperatuur'),
            ('parking', 'Parkeren'),
            ('bonnet', 'Motorkap'),
            ('footBrake', 'Voetrem'),
            ('fuelLeak', 'Brandstoflek'),
            ('tampering', 'Knoeien'),
            ('removing', 'Verwijderd'),
    ]
    return ALARM_CHOICES


def get_gms_status_choices():
    # GMS | Beschrijving                             | BRW | BRW Beschrijving       | AMB | AMB Beschrijving              | POL | POL Beschrijving
    # ---------------------------------------------------------------------------------------------------------------------------------------------
    # 0   | Noodsignaal                              |     |                         |     |                               |     |
    # 1   | Eigen initiatief                         |     |                         |     |                               |     |
    # 2   | Aanvraag spraak                          |  7  | Spraakaanvraag          |  8  | Spraakaanvraag                |     |
    # 3   | Informatievraag                          |     |                         |     |                               |     |
    # 4   | Aanrijdend naar incident                 |  1  | Uitgerukt               |  1  | Verstrek                      |     |
    # 5   | Ter plaatse                              |  2  | Ter plaatse             |  2  | Aankomst                      |     |
    # 6   | Aanrijdend naar bestemming               |  3  | Ingerukt                |  3  | Vertrek met patient           |     |
    # 7   | Binnenkort beschikbaar                   |     |                         |  4  | Aankomst met patient          |     |
    # 8   | Beschikbaar, Onderweg naar standplaats   |  4  | Beschikbaar             |  5  | Vrij melding                  |     |
    # 9   | Op standplaats                           |  5  | Op kazerne              |  6  | Einde rit / op post           |     |
    # 10  | Vertraagd inzetbaar                      |     |                         |     |                               |     |
    # 11  | Buiten dienst                            |  6  | Buiten dienst           |     |                               |     |
    # 12  | Binnenkort in dienst                     |     |                         |     |                               |     |
    # 13  | Aanvraag privégesprek                    |     |                         |  7  | Aanvraag Private call         |     |
    # 14  | aanvraag spraak urgent                   |  9  | Spraakaanvraag urgent   |  9  | Spraakaanvraag urgent         |     |
    # 15  | Opdracht verstrekt                       | 10  | Opdracht verstrekt      | 10  | Opdracht verstrekt            |     |
    # 16  | Alarmering ontvangen                     |     |                         |  0  | Alarmering ontvangen          |     |

    GMS_STATUS_CHOICES = [
            ('0', 'Noodsignaal'),
            ('1', 'Eigen initiatief'),
            ('2', 'Aanvraag spraak'),
            ('3', 'Informatievraag'),
            ('4', 'Aanrijdend naar incident'),
            ('5', 'Ter plaatse'),
            ('6', 'Aanrijdend naar bestemming'),
            ('7', 'Binnenkort beschikbaar'),
            ('8', 'Beschikbaar, Onderweg naar standplaats'),
            ('9', 'Op standplaats'),
            ('10', 'Vertraagd inzetbaar'),
            ('11', 'Buiten dienst'),
            ('12', 'Binnenkort in dienst'),
            ('13', 'Aanvraag privégesprek'),
            ('14', 'aanvraag spraak urgent'),
            ('15', 'Opdracht verstrekt'),
            ('16', 'Alarmering ontvangen'),
    ]
    return GMS_STATUS_CHOICES


def default_tracker_area():
    """
    Geeft een standaardgebied (grofweg Nederland) terug als MultiPolygon.

    Returns:
        MultiPolygon: Standaard geografisch gebied.
    """
    min_lat = 50.475
    max_lat = 53.825
    min_lon = 2.850
    max_lon = 7.550

    polygon = Polygon((
            (min_lon, min_lat),
            (max_lon, min_lat),
            (max_lon, max_lat),
            (min_lon, max_lat),
            (min_lon, min_lat),
    ))
    return MultiPolygon(polygon, srid=4326)


def get_tracker_field_choices():
    """
    Geeft een tuple van:
    - model_fields: Alleen concrete velden uit het Tracker-model
    - all_fields: model_fields + extra virtuele velden

    Returns:
        Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]
    """
    extra_fields = ["age_in_sec", "age_human", "ais_dimensions", "display_name"]
    model_fields = [
            (field.name, field.name)
            for field in Tracker._meta.get_fields()
            if isinstance(field, models.Field) and field.concrete and not field.auto_created
    ]
    all_fields = model_fields + [(field, field) for field in extra_fields]
    return model_fields, all_fields


def get_icon_choises():
    traccar = {
            "default"         : "_Standaard",
            "medic"           : "Ambulance",
            "car"             : "Auto",
            "boat"            : "Boot",
            "fire"            : "Brandweer",
            "bus"             : "Bus",
            "van"             : "Busje",
            "animal"          : "Dier",
            "bicycle"         : "Fiets",
            "helicopter"      : "Helicopter",
            "crane"           : "Kraan",
            "arievisser"      : "KNRM Arie Visser",
            "atlantic"        : "KNRM Atlantic",
            "float"           : "KNRM Float",
            "johannesfrederik": "KNRM Johannes Frederik",
            "khv"             : "KNRM Kusthulpverleningvoertuig",
            "nh1816"          : "KNRM NH 1816",
            "nicolaas"        : "KNRM Nicolaas",
            "valentijn"       : "KNRM Valentijn",
            "mob"             : "MOB Transponder",
            "motorcycle"      : "Motor",
            "offroad"         : "Offroad",
            "person"          : "Persoon",
            "pickup"          : "Pickup",
            "arrow"           : "Pijl",
            "police"          : "Politie",
            "quad"            : "Quad",
            "atvrb"           : "RB ATV",
            "bicyclerb"       : "RB Fiets",
            "lifeguard"       : "RB Person",
            "ribrb"           : "RB RIB",
            "strandjeep"      : "RB Strandjeep",
            "tinn"            : "RB Tinn-Silver",
            "vlet"            : "RB vlet",
            "rwc"             : "RB Waterscooter",
            "rib"             : "Rib",
            "rws"             : "Rijkswaterstaat",
            "ship"            : "Schip",
            "scooter"         : "Scooter",
            "tractor"         : "Tractor",
            "tram"            : "Tram",
            "train"           : "Trein",
            "trolleybus"      : "Trolleybus",
            "plane"           : "Vliegtuig",
            "truck"           : "Vrachtwagen",
    }

    gms = {
            "GMS"        : "==== GMS ====",
            "GMS_AF"     : "GMS Afhijsen",
            "GMS_AFT"    : "GMS Afhijsen tuilijnen",
            "GMS_AC"     : "GMS Algemeen commandant",
            "GMS_AGS"    : "GMS Adviseur gevaarlijke stoffen",
            "GMS_AFO"    : "GMS Airport fire officer",
            "GMS_AH"     : "GMS Arbeidshygiëne",
            "GMS_AL"     : "GMS Autoladder",
            "GMS_AS"     : "GMS Autospuit",
            "GMS_AV"     : "GMS Aanvulling bezetting",
            "GMS_B"      : "GMS Brandweer (In specificatie)",
            "GMS_BA"     : "GMS Blusarm",
            "GMS_BB"     : "GMS Bedrijfsbrandweer",
            "GMS_BBM"    : "GMS Bijzondere blusmiddelen",
            "GMS_BID"    : "GMS Binnen Drone",
            "GMS_BR"     : "GMS Brandweer (in basis)",
            "GMS_BRB"    : "GMS Blusrobot",
            "GMS_BRV"    : "GMS Brandweervaartuig",
            "GMS_BS"     : "GMS Brandstoftank",
            "GMS_BU"     : "GMS Bijzondere uitrusting",
            "GMS_BUD"    : "GMS Buiten Drone",
            "GMS_BVD"    : "GMS Bevelvoerder van Dienst",
            "GMS_BZ"     : "GMS Bevolkingszorg",
            "GMS_C"      : "GMS Coördinator (in basis)",
            "GMS_CC"     : "GMS Communicatie (in specificatie, in basis compagnies commandant)",
            "GMS_CDT"    : "GMS Commandant",
            "GMS_CI"     : "GMS Coördinatie en informatie voorziening",
            "GMS_CO"     : "GMS Commando",
            "GMS_COP"    : "GMS Commandopost",
            "GMS_COPI"   : "GMS Coördinatie plaats incident",
            "GMS_CP"     : "GMS Chemiepakteam",
            "GMS_CT"     : "GMS Crashtender",
            "GMS_CTL"    : "GMS Centralist",
            "GMS_CUGS"   : "GMS Coördinator Uitgangsstelling",
            "GMS_CVD"    : "GMS Commandant van dienst",
            "GMS_DA"     : "GMS Dienstauto",
            "GMS_DAD"    : "GMS Duikadviseur",
            "GMS_DB"     : "GMS Dienstbus",
            "GMS_DC"     : "GMS Ontsmetting / Decontaminatie",
            "GMS_DCU"    : "GMS Decentrale uitgifte",
            "GMS_DF"     : "GMS Defensie",
            "GMS_DK"     : "GMS Daklijnen set",
            "GMS_DP"     : "GMS Dompelpomp",
            "GMS_DPG"    : "GMS Dompelpomp Grote capacitieit",
            "GMS_DPGW"   : "GMS Dompelpomp Grootschalige Watervoorziening",
            "GMS_DPK"    : "GMS Dompelpomp Kleine capacitieit",
            "GMS_DPM"    : "GMS Dompelpomp Middel capacitieit",
            "GMS_DRG"    : "GMS Drone Groot",
            "GMS_DS"     : "GMS Druklucht Schuim",
            "GMS_FBO"    : "GMS Fire bucket operations",
            "GMS_FL"     : "GMS Functionaris logistiek",
            "GMS_FR"     : "GMS First Responder",
            "GMS_G"      : "GMS Groot water (na maat aanduiding)",
            "GMS_GBT"    : "GMS Gemeentelijk Beleid Team",
            "GMS_GGB"    : "GMS Grootschalige geneeskundige bijstand",
            "GMS_GGO"    : "GMS Gebouw gebonden ontsmetting",
            "GMS_GIM"    : "GMS Geo informatie medewerker",
            "GMS_GIMCOPI": "GMS Geo informatie medewerker",
            "GMS_GM"     : "GMS Gereedschap/Materieel",
            "GMS_GO"     : "GMS Grootschalige ontsmetting",
            "GMS_GP"     : "GMS Gaspakteam",
            "GMS_GS"     : "GMS Gevaarlijke stoffen",
            "GMS_GW"     : "GMS Grootschalige watervoorziening",
            "GMS_GWNB"   : "GMS Grootschalige Watervoorziening Natuurbrand Bestrijding",
            "GMS_HA"     : "GMS Haakarmvoertuig",
            "GMS_HB"     : "GMS Havenbedrijf",
            "GMS_HBEB"   : "GMS Hoofd bron- en effectbestrijding",
            "GMS_HC"     : "GMS Handcrew",
            "GMS_HGO"    : "GMS Hoofd grootschalige ontsmetting",
            "GMS_HIN"    : "GMS Hoofd informatie",
            "GMS_HON"    : "GMS Hoofd ondersteuning",
            "GMS_HOVD"   : "GMS Hoofd officier van dienst",
            "GMS_HV"     : "GMS Hulpverlening",
            "GMS_HW"     : "GMS Hoogwerker",
            "GMS_IB"     : "GMS Industrie brandbestrijding",
            "GMS_IC"     : "GMS Informatie coördinator",
            "GMS_ICT"    : "GMS Informatie en communicatie techniek",
            "GMS_IM"     : "GMS Informatiemanager",
            "GMS_IV"     : "GMS Informatie voorziening",
            "GMS_KA"     : "GMS Kantine",
            "GMS_KHV"    : "GMS Kust hulverlening",
            "GMS_KST"    : "GMS Koelstoel",
            "GMS_KW"     : "GMS Kleinschalige watervoorziening",
            "GMS_L"      : "GMS Logistiek (in toevoeging functie anders LO)",
            "GMS_LA"     : "GMS Landelijk adviseur",
            "GMS_LCM"    : "GMS Leiding en Coordi Multi",
            "GMS_LFO"    : "GMS Landelijke faciliteit ontmantelen",
            "GMS_LG"     : "GMS Lifeguard team",
            "GMS_LI"     : "GMS Verlichting",
            "GMS_LO"     : "GMS Logistiek (Bij materieel L bij functie)",
            "GMS_LT"     : "GMS Langstransport",
            "GMS_MA"     : "GMS Medische assistentie",
            "GMS_MAC"    : "GMS Multi Adviseur C2000",
            "GMS_MB"     : "GMS Marine Brandweer",
            "GMS_MF"     : "GMS Multifunctioneel",
            "GMS_MIRG"   : "GMS Maritime Incident Response Group",
            "GMS_MKB"    : "GMS Meldkamer Brandweer",
            "GMS_MOP"    : "GMS Mobiel opstelpunt C2000",
            "GMS_MOR"    : "GMS Metro Ongeval Redgereedschap",
            "GMS_MS"     : "GMS Motorspuit",
            "GMS_MU"     : "GMS Multi CoPI",
            "GMS_MWS"    : "GMS Medewerker sectie",
            "GMS_NA"     : "GMS Noodalarmering",
            "GMS_NB"     : "GMS Natuurbrand",
            "GMS_NO"     : "GMS Noodprocedure",
            "GMS_NR"     : "GMS Nationale reddingsvloot",
            "GMS_NWS"    : "GMS Natuurbrandbestrijding waterschermsysteem",
            "GMS_O"      : "GMS Open",
            "GMS_OG"     : "GMS Omgevingsdienst",
            "GMS_ON"     : "GMS Ondersteuning",
            "GMS_OO"     : "GMS Opleiden trainen oefenen",
            "GMS_OP"     : "GMS Operator",
            "GMS_OPS"    : "GMS Opschaling",
            "GMS_OR"     : "GMS Oppervlakte redding",
            "GMS_OS"     : "GMS Olieschermen",
            "GMS_OSC"    : "GMS On scene commander",
            "GMS_OVD"    : "GMS Officier van dienst",
            "GMS_OVDRAIL": "GMS Officier van dienst ProRail",
            "GMS_OWD"    : "GMS Onderwater Drone",
            "GMS_OZM"    : "GMS Ook zonder dak- en/of bumpermonitor(en)",
            "GMS_PA"     : "GMS Patientvervoer",
            "GMS_PB"     : "GMS Poederblus",
            "GMS_PBM"    : "GMS Persoonlijke beschermingsmiddelen",
            "GMS_PC"     : "GMS Pelotons commandant",
            "GMS_PDB"    : "GMS Paardenbroek",
            "GMS_PLB"    : "GMS Peloton basis",
            "GMS_PLG"    : "GMS Peloton IBGS",
            "GMS_PLL"    : "GMS Peloton logistiek",
            "GMS_PLR"    : "GMS Peloton redding & THV",
            "GMS_PLS"    : "GMS Peloton specialistische blussing",
            "GMS_PLW"    : "GMS Peloton grootschalige watervoorziening",
            "GMS_PM"     : "GMS Personeel/Materieel",
            "GMS_PR"     : "GMS Preventie",
            "GMS_QR"     : "GMS Quick response team",
            "GMS_QRT"    : "GMS Quick response team",
            "GMS_R"      : "GMS Redding (Bij functie of indien onvoldoende ruimte RD in andere gevallen.)",
            "GMS_RA"     : "GMS Regionaal adviseur",
            "GMS_RB"     : "GMS Reddingsbrigade",
            "GMS_RD"     : "GMS Redding (Of R indien onvoldoende ruimte voor RD en in functie)",
            "GMS_RH"     : "GMS Redteam hoogteverschillen",
            "GMS_RI"     : "GMS Rietdak brandbestrijding",
            "GMS_RK"     : "GMS Reddingskussen",
            "GMS_RM"     : "GMS KNRM",
            "GMS_RO"     : "GMS Rellen en ordeverstoring",
            "GMS_ROL"    : "GMS Regionaal operationeel leider",
            "GMS_ROT"    : "GMS Regionaal operationeel team",
            "GMS_RTV"    : "GMS Ramp terrein verlichting",
            "GMS_RV"     : "GMS Redvoertuig",
            "GMS_RWS"    : "GMS Rijkswaterstaat",
            "GMS_S"      : "GMS Specialistisch en na GW of TPB staat een S voor Slangen",
            "GMS_SA"     : "GMS Stroom aggregaat",
            "GMS_SAM"    : "GMS Samenwerking verband IJsselmeer (SAMIJ)",
            "GMS_SB"     : "GMS Schuimblus",
            "GMS_SBB"    : "GMS Scheepsbrandbestrijding",
            "GMS_SH"     : "GMS Specialistische technische hulpverlening (In basis, in spec. STH)",
            "GMS_SI"     : "GMS Snel inzetbaar",
            "GMS_SIB"    : "GMS Scheepsincident bestrijding",
            "GMS_SK"     : "GMS Slagkracht",
            "GMS_SL"     : "GMS Slangen",
            "GMS_SN"     : "GMS Sonar",
            "GMS_SO"     : "GMS Slangen opneem",
            "GMS_SOB"    : "GMS Spoorsloot overbrugging",
            "GMS_SPB"    : "GMS Specialistische blussing",
            "GMS_SR"     : "GMS Surf rescue team",
            "GMS_ST"     : "GMS Stutmaterieel",
            "GMS_STH"    : "GMS Specialistische technische hulpverlening (In spec. in basis SH)",
            "GMS_SV"     : "GMS Schuimvormend middel",
            "GMS_TBO"    : "GMS Team Brand Onderzoek",
            "GMS_TC"     : "GMS Taak commandant",
            "GMS_TD"     : "GMS Technische dienst",
            "GMS_TDV"    : "GMS Team Digitale Verkenning",
            "GMS_TES"    : "GMS Test",
            "GMS_TK"     : "GMS Trekker",
            "GMS_TL"     : "GMS Teamleider",
            "GMS_TN"     : "GMS Tent",
            "GMS_TO"     : "GMS Tactisch officier",
            "GMS_TPB"    : "GMS Tank (put) brandbestrijding",
            "GMS_TS"     : "GMS Tankautospuit",
            "GMS_UHD"    : "GMS Ultra Hoge Druk blussysteem",
            "GMS_USR"    : "GMS USAR",
            "GMS_VC"     : "GMS Verbindingscommando",
            "GMS_VD"     : "GMS Verbindingsdienst",
            "GMS_VE"     : "GMS Verkenningseenheid (Bij functie bij materieel VK)",
            "GMS_VER"    : "GMS Verreiker",
            "GMS_VI"     : "GMS Veetakel installatie",
            "GMS_VK"     : "GMS Verkenning",
            "GMS_VL"     : "GMS Voorlichter",
            "GMS_VN"     : "GMS Ventilator",
            "GMS_VRB"    : "GMS Verkenningsrobot",
            "GMS_VTHS"   : "GMS Veiligheidstester Hoogspanning",
            "GMS_VTLS"   : "GMS Veiligheidstester Laagspanning",
            "GMS_VZ"     : "GMS Verzorging",
            "GMS_WB"     : "GMS Waterbassin",
            "GMS_WC"     : "GMS WC / toiletvoorziening",
            "GMS_WK"     : "GMS Waterkanon",
            "GMS_WO"     : "GMS Waterongevallen",
            "GMS_WOV"    : "GMS Water Ongevallen Vaartuig",
            "GMS_WS"     : "GMS Waterschermsysteem",
            "GMS_WSC"    : "GMS Waterscooter",
            "GMS_WT"     : "GMS Watertank",
            "GMS_ZS"     : "GMS Zichtscherm",
    }
    icons = traccar | gms
    return dict(sorted(icons.items(), key=lambda item: item[1]))


def default_tracker_visible_fields():
    """
    Standaard zichtbare velden voor een tracker.

    Returns:
        List[str]: Lijst met veldnamen.
    """
    return [
            "id", "custom_name", "icon",
            "altitude", "speed", "heading",
            "position_timestamp", "position",
            "age_in_sec", "age_human"
    ]


class TrackerIdentifierType(models.Model):
    """
    Type identificatie (bijv. MMSI, ICAO) dat gekoppeld kan worden aan een tracker.
    """
    code = models.CharField(
            max_length=10,
            primary_key=True,
            validators=[
                    RegexValidator(
                            r'^[A-Z0-9_]+$',
                            'Alleen hoofdletters (A-Z), cijfers (0-9) en underscores (_) zijn toegestaan.'
                    )
            ]
    )
    description = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} | {self.description}"


class TrackerGroup(models.Model):
    """
    Groepering van trackers, eventueel met afgebakend gebied en zichtbare velden.
    """
    smartcode = models.CharField(
            max_length=25,
            db_index=True,
            unique=True,
            help_text="Suffix voor DB-view, na opslaan is deze code niet wijzigbaar. Viewnaam:  v_tracker_group_[smartcode]",
            validators=[
                    RegexValidator(
                            r'^[a-z0-9_]+$',
                            'Alleen kleine letters (a-z), cijfers (0-9) en underscores (_) zijn toegestaan.'
                    )]
    )
    name = models.CharField(max_length=255, unique=True)
    area = gis_models.MultiPolygonField(
            help_text="Laat leeg indien wereldwijde dekking gewenst is.",
            geography=True,
            blank=True,
            null=True,
            srid=4326,
            default=default_tracker_area
    )
    visible_fields = models.JSONField(default=default_tracker_visible_fields, blank=True)

    identifier_types = models.ManyToManyField(
            TrackerIdentifierType,
            blank=True,
            related_name='groups'
    )
    ttl = models.IntegerField(default=(2 * 60), help_text="Leeftijd in minuten, voordat tracker in wordt verborgen in deze groep.")

    class Meta:
        ordering = ['smartcode']

    def clean(self):
        """
        Voorkomt dat de smartcode achteraf gewijzigd wordt.
        """
        if self.pk:
            old = TrackerGroup.objects.filter(pk=self.pk).first()
            if old and self.smartcode != old.smartcode:
                raise ValidationError({'smartcode': _(f'Smartcode "{old.smartcode}" mag niet worden aangepast na creatie.')})

    def __str__(self):
        return f'{self.smartcode} | {self.name}'


class Tracker(models.Model):
    """
    Een volgobject (tracker) met optionele AIS/ADSB eigenschappen en geografische positie.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    custom_name = models.CharField(max_length=255, blank=True, null=True, db_index=True, )
    icon = models.CharField(max_length=32, choices=get_icon_choises(), default='default', blank=True, null=True)
    standplaats = models.ForeignKey(City, on_delete=models.PROTECT, null=True)
    meta_timestamp = models.BigIntegerField(blank=True, null=True, db_index=True, help_text="UNIX tijd in ms")

    alarm_type = models.CharField(max_length=32, choices=get_alarm_choises(), verbose_name='Alarmtype', blank=True, null=True)
    gms_status = models.IntegerField(choices=get_gms_status_choices(), blank=True, null=True)

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
    ais_status = models.CharField(max_length=32, choices=get_alarm_choises(), blank=True, null=True)

    adsb_type = models.CharField(max_length=255, blank=True, null=True)
    adsb_registration = models.CharField(max_length=255, blank=True, null=True)
    adsb_callsign = models.CharField(max_length=255, blank=True, null=True)

    altitude = models.FloatField(blank=True, null=True)
    speed = models.FloatField(blank=True, null=True)
    course = models.FloatField(blank=True, null=True)
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    position_timestamp = models.BigIntegerField(blank=True, null=True, db_index=True, help_text="UNIX tijd in ms")

    groups = models.ManyToManyField(TrackerGroup, related_name='trackers', blank=True)

    class Meta:
        ordering = ['custom_name', 'id']

    @property
    def position_timestamp_display(self):
        """
        Geeft de timestamp weer in leesbaar formaat (UTC).
        """
        if self.position_timestamp:
            dt = datetime.fromtimestamp(self.position_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def position_age_in_sec(self):
        """
        Leeftijdberekening in milliseconden sinds laatste positie.
        """
        if self.position_timestamp:
            return int(time.time() * 1000) - self.position_timestamp
        return None

    @property
    def position_age_display(self):
        """
        Levert de leeftijd in een leesbaar formaat (zoals '2m 30s').
        """
        return self._format_age_display(self.position_age_in_sec)

    @property
    def meta_timestamp_display(self):
        """
        Geeft de meta timestamp weer in leesbaar formaat (UTC).
        """
        if self.meta_timestamp:
            dt = datetime.fromtimestamp(self.meta_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def meta_age_in_sec(self):
        """
        Leeftijdberekening in milliseconden sinds meta_timestamp.
        """
        if self.meta_timestamp:
            return int(time.time() * 1000) - self.meta_timestamp
        return None

    @property
    def meta_age_display(self):
        """
        Levert de leeftijd van de meta_timestamp in een leesbaar formaat.
        """
        return self._format_age_display(self.meta_age_in_sec)

    def _format_age_display(self, age_ms):
        """
        Interne helper om leeftijd weer te geven als '2m 30s'.
        """
        if not age_ms:
            return "-"
        total_seconds = age_ms // 1000
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        if seconds or not parts: parts.append(f"{seconds}s")
        return ' '.join(parts)

    def display_name(self):
        """
        Geeft een leesbare naam voor deze tracker:
        - custom_name indien beschikbaar
        - anders: gekoppelde identifiers
        - anders: UUID
        """
        if self.custom_name:
            return self.custom_name

        identifiers = self.identifiers.all()
        if identifiers.exists():
            return ' | '.join(f"{ident.identkey}" for ident in identifiers)
        return str(self.id)

    def __str__(self):
        return self.display_name()


class TrackerIdentifier(models.Model):
    """
    Identificatie die een externe ID koppelt aan een specifieke tracker.
    """
    external_id = models.CharField(max_length=255, db_index=True)
    identifier_type = models.ForeignKey(TrackerIdentifierType, on_delete=models.PROTECT, related_name='tracker_identifiers')
    tracker = models.ForeignKey(Tracker, on_delete=models.CASCADE, related_name='identifiers')
    identkey = models.CharField(max_length=255, unique=True, editable=False, db_index=True)

    class Meta:
        constraints = [
                UniqueConstraint(fields=['external_id', 'identifier_type'], name='unique_external_id_per_type'),

        ]

        ordering = ['identkey']

    def save(self, *args, **kwargs):
        """
        Zet de external_id om naar hoofdletters, stelt de identkey in,
        en koppelt automatisch relevante groepen aan de tracker.
        """
        self.external_id = self.external_id.upper()
        self.identkey = f"{self.identifier_type.code}_{self.external_id}".upper()
        super().save(*args, **kwargs)

        groups_to_add = self.identifier_type.groups.exclude(
                id__in=self.tracker.groups.values_list('id', flat=True)
        )
        self.tracker.groups.add(*groups_to_add)

    def __str__(self):
        return f"{self.identifier_type.code}: {self.external_id} | {self.tracker.custom_name}"


class TrackerMessage(models.Model):
    """
    Bericht gekoppeld aan een TrackerIdentifier, bevat JSON-inhoud en optioneel een positie.
    """
    tracker_identifier = models.ForeignKey(TrackerIdentifier, on_delete=models.CASCADE, related_name='messages')
    msgtype = models.CharField(max_length=30, default=None)
    sha256_key = models.CharField(max_length=64, primary_key=True)
    content = models.JSONField()
    dbcall = models.JSONField(blank=True, null=True)
    raw = models.JSONField(blank=True, null=True)
    message_timestamp = models.BigIntegerField(db_index=True, help_text="UNIX tijd in milliseconden (UTC)")
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    position_timestamp = models.BigIntegerField(db_index=True, blank=True, null=True, help_text="UNIX tijd in ms")

    class Meta:
        ordering = ['-message_timestamp']

    @property
    def message_timestamp_display(self):
        """
        Geeft de timestamp weer in leesbaar formaat (UTC).
        """
        if self.message_timestamp:
            dt = datetime.fromtimestamp(self.message_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def age_in_sec(self):
        """
        Leeftijdberekening in milliseconden sinds dit bericht.
        """
        if self.message_timestamp:
            return int(time.time() * 1000) - self.message_timestamp
        return None

    @property
    def age_display(self):
        """
        Geeft de leeftijd van het bericht terug als leesbare string.
        """
        age_ms = self.age_in_sec
        if not age_ms:
            return "-"
        total_seconds = age_ms // 1000
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        if seconds or not parts: parts.append(f"{seconds}s")
        return ' '.join(parts)

    def save(self, *args, **kwargs):
        """
        Genereert automatisch een hash van de content indien niet aanwezig.
        """
        if not self.sha256_key and self.content:
            import hashlib, json
            base_str = json.dumps(self.content, sort_keys=True)
            self.sha256_key = hashlib.sha256(base_str.encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tracker_identifier} msg {self.msgtype} at {self.message_timestamp_display}"


class TrackerDecoder(models.Model):
    identifier_type = models.ForeignKey(TrackerIdentifierType, on_delete=models.PROTECT, related_name='decoder_identifiertypes')
    msgtype = models.CharField(max_length=30, default=None)
    mapping = models.JSONField(default=dict, blank=True)
    ttl = models.IntegerField(default=90, verbose_name='Time to live (days)', help_text="Leeftijd in dagen van bericht met dit decodertype, voordat deze wordt verwijderd")

    class Meta:
        ordering = ['identifier_type__code', 'msgtype']

    def __str__(self):
        return f"{self.identifier_type.code} - {self.msgtype}"


class TrackerDecoderField(models.Model):
    name = models.CharField(primary_key=True, max_length=30, default=None, unique=True, validators=[
            RegexValidator(
                    r'^[a-z0-9_]+$',
                    'Alleen kleine letters (a-z), cijfers (0-9) en underscores (_) zijn toegestaan.'
            )])
    dbfield = models.CharField(
            max_length=100,
            blank=True,
            help_text="Kies een veld van het Tracker-model welke overeenkomt met dit decoder veld,<br>laat dit veld leeg om alleen in <i>Tracker messages</i> op te slaan."
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        if self.dbfield:
            return f"{self.name}| {self.dbfield}"
        else:
            return f"{self.name}| No DB field"
