"""
Management command to seed common medications with basic interaction data.

This creates a starter catalog of ~20 common medications for MVP testing.
In production, this would be replaced by API integration.
"""

from django.core.management.base import BaseCommand
from apps.drugs.models import Medication


class Command(BaseCommand):
    help = "Seed common medications with basic interaction data for MVP"

    def handle(self, *args, **options):
        self.stdout.write("Seeding common medications...")

        medications_data = [
            # Blood Pressure / Cardiovascular
            {
                "name": "Lisinopril",
                "generic_name": "lisinopril",
                "medication_type": "drug",
                "food_timing": "anytime",
                "common_dosages": ["5mg tablet", "10mg tablet", "20mg tablet"],
                "known_interactions": {
                    "medication_ids": [],  # Will populate with IDs after creation
                    "warnings": [],
                },
            },
            {
                "name": "Metoprolol",
                "generic_name": "metoprolol",
                "medication_type": "drug",
                "food_timing": "with_food",
                "common_dosages": ["25mg tablet", "50mg tablet", "100mg tablet"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            {
                "name": "Warfarin",
                "generic_name": "warfarin",
                "medication_type": "drug",
                "food_timing": "anytime",
                "common_dosages": ["1mg tablet", "2mg tablet", "5mg tablet"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Pain / Anti-inflammatory
            {
                "name": "Aspirin",
                "generic_name": "acetylsalicylic acid",
                "medication_type": "otc",
                "food_timing": "with_food",
                "common_dosages": ["81mg tablet", "325mg tablet"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            {
                "name": "Ibuprofen",
                "generic_name": "ibuprofen",
                "medication_type": "otc",
                "food_timing": "with_food",
                "common_dosages": ["200mg tablet", "400mg tablet", "600mg tablet"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Thyroid
            {
                "name": "Levothyroxine",
                "generic_name": "levothyroxine",
                "medication_type": "drug",
                "food_timing": "empty_stomach",
                "common_dosages": [
                    "25mcg tablet",
                    "50mcg tablet",
                    "75mcg tablet",
                    "100mcg tablet",
                ],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Diabetes
            {
                "name": "Metformin",
                "generic_name": "metformin",
                "medication_type": "drug",
                "food_timing": "with_food",
                "common_dosages": ["500mg tablet", "850mg tablet", "1000mg tablet"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Cholesterol
            {
                "name": "Atorvastatin",
                "generic_name": "atorvastatin",
                "medication_type": "drug",
                "food_timing": "anytime",
                "common_dosages": [
                    "10mg tablet",
                    "20mg tablet",
                    "40mg tablet",
                    "80mg tablet",
                ],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Acid Reflux
            {
                "name": "Omeprazole",
                "generic_name": "omeprazole",
                "medication_type": "otc",
                "food_timing": "empty_stomach",
                "common_dosages": ["20mg capsule", "40mg capsule"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Vitamins & Supplements
            {
                "name": "Vitamin D",
                "generic_name": "cholecalciferol",
                "medication_type": "supplement",
                "food_timing": "with_food",
                "common_dosages": ["1000 IU", "2000 IU", "5000 IU"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            {
                "name": "Calcium",
                "generic_name": "calcium carbonate",
                "medication_type": "supplement",
                "food_timing": "with_food",
                "common_dosages": ["500mg", "600mg", "1000mg"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            {
                "name": "Fish Oil",
                "generic_name": "omega-3 fatty acids",
                "medication_type": "supplement",
                "food_timing": "with_food",
                "common_dosages": ["1000mg softgel", "1200mg softgel"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            {
                "name": "Magnesium",
                "generic_name": "magnesium",
                "medication_type": "supplement",
                "food_timing": "anytime",
                "common_dosages": ["200mg", "400mg", "500mg"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Mental Health
            {
                "name": "Sertraline",
                "generic_name": "sertraline",
                "medication_type": "drug",
                "food_timing": "anytime",
                "common_dosages": ["25mg tablet", "50mg tablet", "100mg tablet"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Allergy
            {
                "name": "Cetirizine",
                "generic_name": "cetirizine",
                "medication_type": "otc",
                "food_timing": "anytime",
                "common_dosages": ["10mg tablet"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
            # Antibiotics (example)
            {
                "name": "Amoxicillin",
                "generic_name": "amoxicillin",
                "medication_type": "drug",
                "food_timing": "anytime",
                "common_dosages": ["250mg capsule", "500mg capsule"],
                "known_interactions": {"medication_ids": [], "warnings": []},
            },
        ]

        # Create medications
        created_meds = {}
        for med_data in medications_data:
            med, created = Medication.objects.get_or_create(
                name=med_data["name"], defaults=med_data
            )
            created_meds[med.name] = med
            if created:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Created {med.name}"))
            else:
                self.stdout.write(f"  - {med.name} already exists")

        # Now add interaction data
        self.stdout.write("\nAdding interaction data...")

        # Warfarin + Aspirin = bleeding risk
        warfarin = created_meds.get("Warfarin")
        aspirin = created_meds.get("Aspirin")
        if warfarin and aspirin:
            warfarin.known_interactions = {
                "medication_ids": [aspirin.id],
                "warnings": [
                    "Warfarin and Aspirin: Increased risk of bleeding. Monitor closely and consult your doctor."
                ],
            }
            warfarin.save()
            aspirin.known_interactions = {
                "medication_ids": [warfarin.id],
                "warnings": [
                    "Aspirin and Warfarin: Increased risk of bleeding. Monitor closely and consult your doctor."
                ],
            }
            aspirin.save()
            self.stdout.write(
                self.style.WARNING("  ⚠ Added Warfarin <-> Aspirin interaction")
            )

        # Warfarin + Ibuprofen = bleeding risk
        ibuprofen = created_meds.get("Ibuprofen")
        if warfarin and ibuprofen:
            warfarin_interactions = warfarin.known_interactions
            warfarin_interactions["medication_ids"].append(ibuprofen.id)
            warfarin_interactions["warnings"].append(
                "Warfarin and Ibuprofen: Increased risk of bleeding. Avoid NSAIDs if possible."
            )
            warfarin.known_interactions = warfarin_interactions
            warfarin.save()

            ibuprofen.known_interactions = {
                "medication_ids": [warfarin.id],
                "warnings": [
                    "Ibuprofen and Warfarin: Increased risk of bleeding. Avoid NSAIDs if possible."
                ],
            }
            ibuprofen.save()
            self.stdout.write(
                self.style.WARNING("  ⚠ Added Warfarin <-> Ibuprofen interaction")
            )

        # Calcium + Levothyroxine = absorption issue (must space apart)
        calcium = created_meds.get("Calcium")
        levothyroxine = created_meds.get("Levothyroxine")
        if calcium and levothyroxine:
            calcium.known_interactions = {
                "medication_ids": [levothyroxine.id],
                "warnings": [
                    "Calcium and Levothyroxine: Calcium can reduce thyroid medication absorption. "
                    "Take levothyroxine at least 4 hours before or after calcium."
                ],
            }
            calcium.save()
            levothyroxine.known_interactions = {
                "medication_ids": [calcium.id],
                "warnings": [
                    "Levothyroxine and Calcium: Calcium can reduce thyroid medication absorption. "
                    "Take levothyroxine at least 4 hours before or after calcium."
                ],
            }
            levothyroxine.save()
            self.stdout.write(
                self.style.WARNING("  ⚠ Added Calcium <-> Levothyroxine interaction")
            )

        # Fish Oil + Warfarin = potential bleeding increase
        fish_oil = created_meds.get("Fish Oil")
        if fish_oil and warfarin:
            warfarin_interactions = warfarin.known_interactions
            warfarin_interactions["medication_ids"].append(fish_oil.id)
            warfarin_interactions["warnings"].append(
                "Warfarin and Fish Oil: May increase bleeding risk. Discuss with your doctor."
            )
            warfarin.known_interactions = warfarin_interactions
            warfarin.save()

            fish_oil.known_interactions = {
                "medication_ids": [warfarin.id],
                "warnings": [
                    "Fish Oil and Warfarin: May increase bleeding risk. Discuss with your doctor."
                ],
            }
            fish_oil.save()
            self.stdout.write(
                self.style.WARNING("  ⚠ Added Fish Oil <-> Warfarin interaction")
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Seeded {len(created_meds)} medications with interaction data"
            )
        )
        self.stdout.write(
            "Run this command again to refresh interaction data if models change.\n"
        )
