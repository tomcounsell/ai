# Medication & Supplement Tracker

## Overview

A web application that helps users catalog their medicines and supplements, tracks interactions between them, and suggests optimal timing based on the user's eating schedule.

## Current Status

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1: Core Catalog | ✅ Complete | Models, admin, seed data |
| Phase 2: API Integration | 🔄 In Progress | RxNorm + OpenFDA done, AI fallback next |
| Phase 3: Smart Scheduling | ⏳ Planned | |
| Phase 4: Polish | ⏳ Planned | |

### What's Built

**Models** (`apps/drugs/models.py`):
- `Medication` - name, generic_name, type, food_timing, known_interactions (JSON)
- `UserMedication` - links user to medication with dosage, frequency, preferences
- `UserMealSchedule` - user's meal times for scheduling

**API Clients** (`apps/integration/`):
- `rxnorm/client.py` - Drug normalization, RxCUI lookup, autocomplete
- `openfda/client.py` - Drug labels, interaction warnings, food interactions

**Services** (`apps/drugs/services/`):
- `drug_lookup.py` - Lazy loading: checks DB first, fetches from APIs if missing
- `interactions.py` - Checks user's medications for known interactions
- `scheduler.py` - Generates daily medication schedule based on meal times

**Seed Data**:
- 16 common medications with hardcoded interaction data
- Management command: `python manage.py seed_medications`

### What's Next

1. **AI Fallback for Supplements** - RxNorm/OpenFDA don't have good data for supplements (CoQ10, Quercetin, etc.). Need AI agent to fill gaps.
2. **MedicationInteraction Model** - Currently interactions are stored as JSON on Medication. Need proper M2M model for better querying.
3. **User Interface** - No UI yet, just admin and API.

---

## Core Features

### 1. Medication Catalog
- Add medications (prescription drugs, OTC medicines, supplements, vitamins)
- Capture per medication:
  - Name (brand and/or generic)
  - Dosage and form (tablet, capsule, liquid, etc.)
  - Frequency (once daily, twice daily, as needed, etc.)
  - User notes (why they take it, prescribing doctor, etc.)
- Edit and remove medications from personal list
- Mark medications as active/inactive (paused, completed course)

### 2. Interaction Database
- **Lazy population strategy**: Don't pre-populate; build database as users add medications
- When a user adds a new medication:
  1. Check if we have interaction data for this drug
  2. If not, fetch interactions from external API/AI
  3. Cache interaction data for future users
- Store interaction types:
  - Drug-drug interactions
  - Drug-food interactions (grapefruit, dairy, alcohol, etc.)
  - Drug-supplement interactions
  - Timing conflicts (e.g., calcium blocks thyroid medication absorption)
- Severity levels: Minor, Moderate, Major, Contraindicated

### 3. Eating Schedule
- User defines their typical meal times:
  - Breakfast, Lunch, Dinner, Snacks
  - Fasting windows (if applicable)
- Flexible scheduling (weekday vs weekend patterns)

### 4. Timing Recommendations
- Algorithm considers:
  - Required spacing between specific drugs
  - Food requirements (take with food, take on empty stomach)
  - Interaction avoidance windows
  - User's meal schedule
- Output: Suggested daily schedule with medication times
- Alerts when conflicts cannot be resolved

### 5. Interaction Alerts
- Real-time warnings when adding a medication that interacts with existing ones
- Dashboard showing all active interactions
- Severity-based prioritization

---

## Data Model

### Current Models (Implemented)

```python
# apps/drugs/models.py

class Medication(Timestampable, models.Model):
    name = models.CharField(max_length=200)
    generic_name = models.CharField(max_length=200, blank=True)
    medication_type = models.CharField(choices=[drug, supplement, vitamin, otc, herbal])
    food_timing = models.CharField(choices=[with_food, empty_stomach, anytime])
    known_interactions = models.JSONField(default=dict)  # {medication_ids, warnings, sources}
    common_dosages = models.JSONField(default=list)

class UserMedication(Timestampable, Authorable, models.Model):
    user = models.ForeignKey(User)
    medication = models.ForeignKey(Medication)
    dosage = models.CharField()
    frequency = models.CharField()
    time_preference = models.CharField(choices=[morning, evening, anytime])
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

class UserMealSchedule(Timestampable, Authorable, models.Model):
    user = models.ForeignKey(User)
    breakfast_time = models.TimeField()
    lunch_time = models.TimeField()
    dinner_time = models.TimeField()
```

### Planned Models (Not Yet Implemented)

```python
# Proper M2M for interactions (better than JSON field)
class MedicationInteraction(models.Model):
    medication_a = models.ForeignKey(Medication)
    medication_b = models.ForeignKey(Medication)
    severity = models.CharField(choices=[minor, moderate, major, contraindicated])
    description = models.TextField()
    recommendation = models.TextField()
    spacing_hours = models.IntegerField(null=True)  # Min hours between doses
    source = models.CharField()  # openfda, dailymed, ai, manual
    verified = models.BooleanField(default=False)

class FoodInteraction(models.Model):
    medication = models.ForeignKey(Medication)
    food_type = models.CharField()  # grapefruit, dairy, alcohol, caffeine, etc.
    effect = models.CharField()  # reduces_absorption, increases_absorption, dangerous
    description = models.TextField()
```

### Lazy Loading Flow

```
User adds "Lisinopril" to their medications
    ↓
DrugLookupService.lookup_drug("Lisinopril")
    ↓
Check Medication.objects.filter(name__iexact="Lisinopril")
    ↓ Not found
RxNormClient.normalize_drug_name("Lisinopril") → RxCUI: 29046
    ↓
OpenFDAClient.get_drug_label_by_rxcui("29046") → interaction warnings
    ↓
Create Medication(name="Lisinopril", known_interactions={...})
    ↓
InteractionChecker.check_user_interactions() → Display warnings
```

---

## External Data Sources

### Implemented ✅

#### RxNorm API (NLM)
- **Client**: `apps/integration/rxnorm/client.py`
- **Purpose**: Drug identification and normalization
- **License**: Free, no license needed
- **Rate Limit**: 20 requests/second per IP
- **Caching**: 24 hours (Django cache)
- **Methods**:
  - `search_drugs(name)` - Find drugs by name
  - `get_spelling_suggestions(term)` - Autocomplete
  - `get_rxcui(name)` - Get RxCUI identifier
  - `get_drug_properties(rxcui)` - Drug details
  - `get_related_drugs(rxcui)` - Brand/generic mapping
  - `normalize_drug_name(user_input)` - Full normalization flow
- **Attribution Required**:
  > "This product uses publicly available data from the U.S. National Library of Medicine (NLM)..."

#### OpenFDA API
- **Client**: `apps/integration/openfda/client.py`
- **Purpose**: Drug labels, adverse events, interaction warnings
- **License**: CC0 1.0 (public domain)
- **Rate Limit**: 240 req/min (with API key)
- **Caching**: 24 hours (Django cache)
- **Methods**:
  - `get_drug_label(name)` - Label by drug name
  - `get_drug_label_by_rxcui(rxcui)` - Label by RxCUI
  - `get_adverse_events(name)` - Adverse event reports
  - `search_drugs(query)` - Search drugs
- **Parsed Fields**: brand_name, generic_name, drug_interactions, warnings, food_interactions

### Planned 🔄

#### DailyMed API (NLM)
- **URL**: https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm
- **Purpose**: Current FDA-approved drug labeling (SPL format)
- **Use For**:
  - Full prescribing information
  - Structured drug interaction sections
  - Can query by RxCUI (links to RxNorm)
- **Formats**: XML and JSON

#### AI Fallback (Claude/PydanticAI)
- **Purpose**: Fill gaps when APIs don't have data (supplements, herbals)
- **Approach**:
  - Prompt template for interaction lookup
  - Parse response into structured format
  - Flag as "AI-generated, verify with pharmacist"
  - Cache permanently for future users
- **Implementation**: `apps/ai/agent/drug_interactions.py` using PydanticAI

### Not Available / Not Usable

- **NLM Drug Interaction API**: Discontinued January 2024
- **DrugBank**: Requires commercial license
- **DDInter**: CC BY-NC-SA license (NonCommercial only)

---

## Implementation Phases

### Phase 1: Core Catalog ✅ Complete

- [x] Medication model with basic fields
- [x] UserMedication model linking users to medications
- [x] UserMealSchedule model for eating times
- [x] Django admin for all models
- [x] Seed data for 16 common medications
- [x] Basic interaction checking service (JSON-based)
- [x] Basic scheduling service

### Phase 2: API Integration 🔄 In Progress

**Completed:**
- [x] RxNorm client (`apps/integration/rxnorm/client.py`)
  - [x] Drug name search (`/drugs` endpoint)
  - [x] Autocomplete (`/spellingsuggestions`)
  - [x] Get RxCUI for a drug name (`/rxcui`)
  - [x] Fetch drug properties (`/rxcui/{rxcui}/properties`)
  - [x] Map brand names to generic (`/rxcui/{rxcui}/related`)
  - [x] 24-hour response caching
- [x] OpenFDA client (`apps/integration/openfda/client.py`)
  - [x] Fetch drug label by name/RxCUI
  - [x] Parse `drug_interactions` field
  - [x] Extract food interaction info
  - [x] 24-hour response caching
- [x] Lazy loading service (`apps/drugs/services/drug_lookup.py`)
  - [x] Check database first
  - [x] Fetch from RxNorm + OpenFDA if missing
  - [x] Create Medication record with gathered data

**Remaining:**
- [ ] DailyMed client (structured interaction parsing)
- [ ] AI fallback agent for supplements
  - [ ] PydanticAI agent with structured output
  - [ ] Prompt template for interaction lookup
  - [ ] Flag AI-generated data in database
- [ ] MedicationInteraction model (replace JSON field)
- [ ] FoodInteraction model
- [ ] Interaction aggregator service
  - [ ] Orchestrate calls to all sources
  - [ ] Merge/deduplicate interaction data
  - [ ] Assign confidence levels by source
- [ ] Add NLM attribution to UI

### Phase 3: Smart Scheduling ⏳ Planned

- [ ] Enhanced scheduling algorithm
  - [ ] Consider drug-drug spacing requirements
  - [ ] Handle interaction avoidance windows
  - [ ] Optimize for user convenience
- [ ] Daily plan generation with conflict detection
- [ ] Conflict resolution suggestions
- [ ] Schedule regeneration on changes

### Phase 4: Polish ⏳ Planned

- [ ] User interface (HTMX-based)
  - [ ] Medication list page
  - [ ] Add medication with autocomplete
  - [ ] Interactions dashboard
  - [ ] Daily schedule view
- [ ] Mobile optimization
- [ ] Export/print schedules
- [ ] User feedback on interactions
- [ ] Admin verification workflow
- [ ] Safety disclaimers

---

## Codebase Architecture

```
apps/drugs/                          # Main application
├── __init__.py
├── admin.py                         # Django admin
├── apps.py
├── models.py                        # Medication, UserMedication, UserMealSchedule
├── services/
│   ├── __init__.py
│   ├── drug_lookup.py               # Lazy loading from APIs
│   ├── interactions.py              # Interaction checking
│   └── scheduler.py                 # Daily schedule generation
├── management/
│   └── commands/
│       └── seed_medications.py      # Seed 16 common medications
├── fixtures/
│   └── test_user_medications.json   # Test data (raw JSON, not Django fixture)
├── migrations/
└── tests/

apps/integration/rxnorm/             # RxNorm API client ✅
├── __init__.py
└── client.py

apps/integration/openfda/            # OpenFDA API client ✅
├── __init__.py
└── client.py

apps/integration/dailymed/           # DailyMed API client (planned)
├── __init__.py
├── client.py
└── parsers.py                       # SPL XML parsing

apps/ai/agent/                       # AI agents (planned)
└── drug_interactions.py             # AI fallback for supplements
```

---

## Safety & Disclaimers

### Critical Requirements

1. **Prominent disclaimer**: "This tool is for informational purposes only. Always consult your doctor or pharmacist."
2. **Not medical advice**: Make clear this doesn't replace professional guidance
3. **Encourage verification**: Prompt users to verify AI-generated interactions
4. **Emergency contacts**: Link to poison control, suggest pharmacist consultation
5. **No liability**: Clear terms of service

### Data Accuracy

- Track data source for every interaction (openfda, dailymed, ai, manual)
- Flag AI-generated vs. verified data
- Allow users to report inaccuracies
- Admin review queue for flagged issues

---

## Future Features (Out of Scope)

- Extended medication types (topicals, injectables, medical devices)
- Variable schedules (weekday vs weekend)
- Share medication list with caregiver or doctor
- Medication history tracking
- Refill reminders
- Multi-user households
