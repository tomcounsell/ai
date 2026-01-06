# Medication & Supplement Tracker

## Overview

A web application that helps users catalog their medicines and supplements, tracks interactions between them, and suggests optimal timing based on the user's eating schedule.

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

### User-Facing Models

```
UserMedication
├── user (FK)
├── medication (FK to Medication)
├── dosage (CharField)
├── frequency (CharField or structured)
├── time_preference (morning, evening, with meals, etc.)
├── is_active (Boolean)
├── notes (TextField)
├── started_at (DateTimeField)
├── ended_at (DateTimeField, nullable)

Medication
├── name (CharField)
├── generic_name (CharField, nullable)
├── medication_type (drug, supplement, vitamin, herbal)
├── common_dosages (JSONField)
├── food_interaction (with_food, empty_stomach, no_preference)
├── external_ids (JSONField - RxNorm, NDC, etc.)

MedicationInteraction
├── medication_a (FK)
├── medication_b (FK)
├── severity (minor, moderate, major, contraindicated)
├── description (TextField)
├── recommendation (TextField)
├── spacing_hours (Integer, nullable - min hours between)
├── source (CharField - where data came from)
├── verified (Boolean)

FoodInteraction
├── medication (FK)
├── food_type (grapefruit, dairy, alcohol, caffeine, high_fat, etc.)
├── effect (reduces_absorption, increases_absorption, dangerous, etc.)
├── description (TextField)

UserMealSchedule
├── user (FK)
├── meal_type (breakfast, lunch, dinner, snack)
├── typical_time (TimeField)
├── days_of_week (JSONField or M2M)

UserMedicationSchedule (generated)
├── user (FK)
├── user_medication (FK)
├── suggested_time (TimeField)
├── relation_to_meal (before, with, after, independent)
├── notes (TextField)
```

### Interaction Population Flow

```
User adds "Lisinopril" to their medications
    ↓
System checks: Do we have Lisinopril in Medication table?
    ↓ No
Query external source (API or AI) for:
    - Drug info
    - Known interactions
    - Food interactions
    ↓
Store Medication record
Store all MedicationInteraction records
Store all FoodInteraction records
    ↓
Check user's existing medications for interactions
    ↓
Display warnings and update schedule
```

---

## External Data Sources

### Available Free APIs (Commercial Use Allowed)

#### 1. RxNorm API (NLM)
- **URL**: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html
- **Purpose**: Drug identification and normalization
- **License**: Free, no license needed
- **Rate Limit**: 20 requests/second per IP
- **Caching**: Recommended 12-24 hours
- **Attribution Required**:
  > "This product uses publicly available data from the U.S. National Library of Medicine (NLM)..."
- **Use For**: Matching user input to standard drug names, getting RxCUI identifiers

#### 2. OpenFDA API
- **URL**: https://open.fda.gov/apis/drug/
- **Purpose**: Drug labels, adverse events, recalls, NDC directory
- **License**: CC0 1.0 (public domain) - commercial use allowed
- **Rate Limit**: 240 req/min, 120k/day (with free API key)
- **Use For**:
  - Drug labeling data (contains interaction warnings in label text)
  - Adverse event reports
  - Product information
- **Note**: No structured interaction database, but labels contain interaction sections

#### 3. DailyMed API (NLM)
- **URL**: https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm
- **Purpose**: Current FDA-approved drug labeling (SPL format)
- **License**: Public access, free
- **Use For**:
  - Full prescribing information
  - Drug interaction sections from official labels
  - Can query by RxCUI (links to RxNorm)
- **Formats**: XML and JSON

### Not Available / Not Usable

- **NLM Drug Interaction API**: Discontinued January 2024
- **DrugBank**: Requires commercial license
- **DDInter**: CC BY-NC-SA license (NonCommercial only - cannot use)

### AI-Assisted Lookup (Claude)
- Use when no structured data available from APIs
- Summarize known interactions from training data
- Flag as "AI-generated, verify with pharmacist"
- Cache results permanently for future users
- Allow admin verification to upgrade confidence

### Recommended Integration Strategy

1. **RxNorm**: Primary drug identification - normalize all user input
2. **DailyMed**: Fetch official label, parse interaction/warning sections
3. **OpenFDA**: Supplement with adverse event data, additional labeling
4. **Claude AI**: Fill gaps when APIs don't have data, with disclaimers

---

## API Integration TODO

### Phase 2 Integration Tasks

- [ ] **RxNorm Service** (`services/rxnorm.py`)
  - [ ] Implement drug name search (`/drugs` endpoint)
  - [ ] Implement autocomplete for drug entry (`/spellingsuggestions`)
  - [ ] Get RxCUI for a drug name (`/rxcui`)
  - [ ] Fetch drug properties (`/rxcui/{rxcui}/properties`)
  - [ ] Map brand names to generic (`/rxcui/{rxcui}/related`)
  - [ ] Add 24-hour response caching
  - [ ] Add NLM attribution to UI

- [ ] **DailyMed Service** (`services/dailymed.py`)
  - [ ] Fetch SPL by RxCUI (`/spls.json?rxcui=`)
  - [ ] Parse drug interaction section from SPL XML
  - [ ] Parse warnings/precautions section
  - [ ] Extract food interaction info from label
  - [ ] Store parsed interactions in database

- [ ] **OpenFDA Service** (`services/openfda.py`)
  - [ ] Register for API key
  - [ ] Fetch drug label by name/NDC (`/drug/label.json`)
  - [ ] Parse `drug_interactions` field from label
  - [ ] Fetch adverse events for a drug (`/drug/event.json`)
  - [ ] Implement rate limiting (240/min)

- [ ] **AI Interaction Service** (`services/ai_interactions.py`)
  - [ ] Prompt template for interaction lookup
  - [ ] Parse Claude response into structured format
  - [ ] Flag results as AI-generated in database
  - [ ] Fallback when APIs return no data

- [ ] **Interaction Aggregator** (`services/interactions.py`)
  - [ ] Orchestrate calls to all sources
  - [ ] Merge/deduplicate interaction data
  - [ ] Assign confidence levels by source
  - [ ] Store results with source attribution

---

## User Interface

### Pages

1. **My Medications** - List view with add/edit/remove
2. **Add Medication** - Search/autocomplete, dosage entry
3. **Interactions Dashboard** - Visual display of all interactions
4. **My Schedule** - Meal times configuration
5. **Daily Plan** - Generated medication schedule with times
6. **Alerts** - Current warnings requiring attention

### Key UX Considerations

- Search with autocomplete (fuzzy matching on drug names)
- Clear severity indicators (color-coded)
- Mobile-friendly (users check this throughout day)
- Print/export daily schedule
- Reminder integration (future: push notifications)

---

## Safety & Disclaimers

### Critical Requirements

1. **Prominent disclaimer**: "This tool is for informational purposes only. Always consult your doctor or pharmacist."
2. **Not medical advice**: Make clear this doesn't replace professional guidance
3. **Encourage verification**: Prompt users to verify AI-generated interactions
4. **Emergency contacts**: Link to poison control, suggest pharmacist consultation
5. **No liability**: Clear terms of service

### Data Accuracy

- Track data source for every interaction
- Flag AI-generated vs. verified data
- Allow users to report inaccuracies
- Admin review queue for flagged issues

---

## Implementation Phases

### Phase 1: Core Catalog
- User medication list (CRUD)
- Basic medication database
- Manual interaction entry (admin)
- Simple meal schedule

### Phase 2: Automated Interactions
- RxNorm integration for drug lookup
- AI-powered interaction fetching
- Caching and storage of results
- Basic interaction warnings

### Phase 3: Smart Scheduling
- Scheduling algorithm
- Daily plan generation
- Conflict resolution suggestions

### Phase 4: Polish
- Mobile optimization
- Export/print schedules
- User feedback on interactions
- Admin verification workflow

---

## Technical Considerations

### Codebase Architecture

The medication tracker integrates into cuttlefish following existing patterns:

```
apps/drugs/                          # Main application
├── __init__.py
├── admin.py                         # Django admin for Medication, Interaction models
├── apps.py
├── models/
│   ├── __init__.py
│   ├── medication.py                # Medication, MedicationInteraction, FoodInteraction
│   └── user_medication.py           # UserMedication, UserMealSchedule, UserMedicationSchedule
├── services/
│   ├── __init__.py
│   ├── interactions.py              # Aggregator: orchestrates API calls, merges results
│   └── scheduler.py                 # Timing algorithm for daily plan generation
├── views/
│   ├── __init__.py
│   ├── medications.py               # CRUD views (MainContentView pattern)
│   ├── schedule.py                  # Meal schedule, daily plan views
│   └── partials/                    # HTMX partial views
│       ├── medication_list.py
│       ├── interaction_alerts.py
│       └── daily_plan.py
├── urls.py
├── migrations/
└── tests/
    ├── __init__.py
    ├── factories.py
    ├── test_models/
    ├── test_views/
    └── test_services/

apps/integration/rxnorm/             # RxNorm API client
├── __init__.py
├── client.py                        # RxNormClient (async, like QuickBooksClient)
└── tests/
    └── test_client.py

apps/integration/openfda/            # OpenFDA API client
├── __init__.py
├── client.py                        # OpenFDAClient
└── tests/
    └── test_client.py

apps/integration/dailymed/           # DailyMed API client
├── __init__.py
├── client.py                        # DailyMedClient
├── parsers.py                       # SPL XML parsing for interaction sections
└── tests/
    └── test_client.py

apps/ai/agent/                       # AI interaction lookup (extends existing)
├── drug_interactions.py             # AI agent for interaction data when APIs lack info

apps/public/templates/drugs/         # Templates (in central template location)
├── medication_list.html
├── medication_form.html
├── medication_detail.html
├── interactions_dashboard.html
├── schedule_form.html
├── daily_plan.html
└── partials/
    ├── medication_card.html
    ├── interaction_alert.html
    └── schedule_row.html
```

### Integration Patterns

**API Clients** (`apps/integration/*/client.py`):
- Async clients using `aiohttp` (matches QuickBooksClient pattern)
- Response caching with configurable TTL
- Proper error handling and logging

**Views** (`apps/drugs/views/`):
- Inherit from `MainContentView` for full pages
- Use `HTMXView` for partial/component responses
- Follow existing URL patterns in `apps/public/urls.py`

**Models** (`apps/drugs/models/`):
- Use behavior mixins from `apps/common/behaviors/` (Timestampable, Authorable)
- Store external IDs (RxCUI, NDC) in JSONField for flexibility
- Track data source and verification status on interaction records

**AI Integration** (`apps/ai/agent/drug_interactions.py`):
- PydanticAI agent with `Agent` prefix (e.g., `AgentDrugInteractionLookup`)
- Returns structured interaction data
- Results flagged as AI-generated in database

### URL Structure

```python
# apps/drugs/urls.py
urlpatterns = [
    path("medications/", MedicationListView.as_view(), name="medication_list"),
    path("medications/add/", MedicationCreateView.as_view(), name="medication_add"),
    path("medications/<int:pk>/", MedicationDetailView.as_view(), name="medication_detail"),
    path("medications/<int:pk>/edit/", MedicationUpdateView.as_view(), name="medication_edit"),
    path("medications/<int:pk>/delete/", MedicationDeleteView.as_view(), name="medication_delete"),
    path("interactions/", InteractionsDashboardView.as_view(), name="interactions_dashboard"),
    path("schedule/", ScheduleView.as_view(), name="schedule"),
    path("schedule/plan/", DailyPlanView.as_view(), name="daily_plan"),
]

# Include in main urls.py
path("drugs/", include("apps.drugs.urls")),
```

### Performance

- Cache API responses: RxNorm (24h), DailyMed (24h), OpenFDA (24h)
- Background task for fetching new interaction data (avoid blocking user)
- Pre-compute daily schedules, update on medication/meal changes
- Database indexes on RxCUI, medication name for fast lookups

---

## Future Features (Out of Scope)

The following are not part of the initial implementation but may be considered later:

- **Extended medication types**: Topicals, injectables, medical devices
- **Variable schedules**: Different schedules for weekdays vs weekends
- **Sharing**: Share medication list with caregiver or doctor
- **Medication history**: Track changes over time, past medications
- **Refill tracking**: Reminders when running low
- **Multi-user households**: Family medication tracking under one account
