# tests.py
from django.test import TestCase
from gpstracking.models import Tracker, TrackerGroup, TrackerIdentifier, TrackerIdentifierType

def debug_tracker_state(tracker):
    print(f"\n=== Tracker: {tracker.screen_name} ===")
    print("Identifiers:", list(tracker.identifiers.values_list('identifier_type__name', 'external_id')))
    print("Groups:", list(tracker.groups.values_list('name', flat=True)))
    print("===============================\n")

class TrackerGroupLogicTest(TestCase):
    def setUp(self):
        self.group = TrackerGroup.objects.create(name="Zichtbare Schepen")
        self.type_mmsi = TrackerIdentifierType.objects.create(name="MMSI")
        self.group.identifier_types.add(self.type_mmsi)

        self.tracker = Tracker.objects.create(screen_name="Testboot", icon="ship")
        self.identifier = TrackerIdentifier.objects.create(
            external_id="123456789",
            identifier_type=self.type_mmsi,
            tracker=self.tracker
        )

    def test_tracker_linked_via_identifier_type(self):
        debug_tracker_state(self.tracker)
        self.assertIn(
            self.group,
            self.tracker.groups.all(),
            msg=f"Tracker zou aan de groep moeten zijn gekoppeld via identifier type {self.type_mmsi.name}"
        )

    def test_removal_of_identifier_type_removes_group_only_if_not_direct(self):
        self.tracker.groups.add(self.group)
        self.group.identifier_types.remove(self.type_mmsi)
        self.tracker.refresh_from_db()
        debug_tracker_state(self.tracker)

        self.assertIn(
            self.group,
            self.tracker.groups.all(),
            msg="Directe koppeling is onterecht verwijderd na wijziging identifier types"
        )

    def test_group_removed_when_only_linked_via_identifier_type(self):
        tracker2 = Tracker.objects.create(screen_name="Uniek", icon="icon")
        TrackerIdentifier.objects.create(
            external_id="888888888",
            identifier_type=self.type_mmsi,
            tracker=tracker2
        )
        self.group.identifier_types.remove(self.type_mmsi)
        tracker2.refresh_from_db()
        debug_tracker_state(tracker2)

        self.assertNotIn(
            self.group,
            tracker2.groups.all(),
            msg="Groep is nog steeds gekoppeld aan tracker2 zonder directe koppeling of geldige identifier"
        )

    def test_add_identifier_type_links_existing_tracker(self):
        new_group = TrackerGroup.objects.create(name="Luchtverkeer")
        new_type = TrackerIdentifierType.objects.create(name="ICAO")
        TrackerIdentifier.objects.create(
            external_id="ABC123",
            identifier_type=new_type,
            tracker=self.tracker
        )
        new_group.identifier_types.add(new_type)
        self.tracker.refresh_from_db()
        debug_tracker_state(self.tracker)
        self.assertIn(
            new_group,
            self.tracker.groups.all(),
            msg="Nieuwe groep is niet gekoppeld aan tracker ondanks matchende identifier type"
        )

    def test_remove_identifier_unlinks_tracker_if_last_matching(self):
        self.identifier.delete()
        self.tracker.refresh_from_db()
        debug_tracker_state(self.tracker)

        self.assertNotIn(
            self.group,
            self.tracker.groups.all(),
            msg="Groep is niet verwijderd ondanks dat laatste identifier verwijderd is"
        )