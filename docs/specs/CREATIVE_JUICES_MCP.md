# Creative Juices MCP Specification

## Implementation Status

### Phase 1 (✅ COMPLETED - Basic Functionality)
- ✅ MCP server implementation using FastMCP
- ✅ Three creative thinking tools: `get_inspiration`, `think_outside_the_box`, `reality_check`
- ✅ Concrete word lists for tangible metaphors (inspiring and out-of-the-box categories)
- ✅ Elon Musk thinking frameworks integration
- ✅ Django integration (apps/ai/mcp/)
- ✅ Basic tests for server functionality

### Phase 2 (🔄 TODO - Enhancements)
- ⬜ Add parameter support (count, intensity)
- ⬜ Add Pydantic validation
- ⬜ Add prompts via @mcp.prompt()
- ⬜ Standardize error handling patterns
- ⬜ Add comprehensive logging

### Phase 3 (⬜ TODO - Advanced Features)
- ⬜ Extended word categories (sci-fi, primitive, nurturing)
- ⬜ User-configurable word lists
- ⬜ Session history tracking
- ⬜ Analytics on tool usage
- ⬜ Claude Desktop DXT package
- ⬜ PydanticAI example integration

## Overview

**Name**: Creative Juices MCP
**Purpose**: Provides randomness tools to encourage out-of-the-box thinking through concrete verb-noun combinations and strategic frameworks
**Key Value Proposition**: Break out of predictable AI responses by injecting unexpected tangible metaphors and Elon Musk's proven thinking frameworks

## Core Functionality

### Tools

1. **`get_inspiration()`** - Early-stage creative framing with gentle, concrete metaphors
2. **`think_outside_the_box()`** - Mid-stage divergent thinking with intense, dramatic concepts
3. **`reality_check()`** - Grounding tool using Elon Musk's thinking frameworks

### Prompts
- None currently implemented (Phase 2 enhancement)

## Tool Definitions

### get_inspiration

```
Description: Use at the start of creative or problem-solving tasks to frame challenges
             in unexpected ways. Provides gentle, concrete verb-noun combinations.
Parameters: None
Returns:
  - sparks: list of 3 verb-noun combinations (e.g., "painting-shoe", "baking-door")
  - instruction: "Use these unexpected combinations as initial lenses:"
Example Use Case: Beginning creative work, need unconventional starting points
Word Lists: "inspiring" category - everyday human actions, animal behaviors, natural phenomena
```

### think_outside_the_box

```
Description: Use mid-conversation when exploration has stalled or thinking has become
             too linear. Forces radical divergence with intense verb-noun combinations.
Parameters: None
Returns:
  - sparks: list of 3 verb-noun combinations (e.g., "crushing-fire", "burning-storm")
  - instruction: "Shatter your assumptions with these:"
Example Use Case: Breaking out of convergent patterns, need dramatic perspective shift
Word Lists: "out_of_the_box" category - destructive actions, violent phenomena, sci-fi tech
```

### reality_check

```
Description: Ground creative thinking in reality while maintaining openness.
             Pressure-test wild ideas against Elon Musk's strategic frameworks.
Parameters: None
Returns:
  - questions: list of 4 questions (one from each framework)
  - frameworks: list of framework names ["first_principles", "limit_thinking", "platonic_ideal", "optimization"]
  - instruction: "Ground your thinking with one question from each Musk framework:"
Example Use Case: Validating assumptions, identifying what actually matters
Frameworks:
  - First Principles Thinking: Strip to fundamental truths
  - Think in the Limit: Scale to extremes
  - Platonic Ideal: Perfect solution first
  - Five-Step Optimization: Question, delete, optimize, accelerate, automate
```

## Implementation Details

### Architecture

```
apps/ai/mcp/
├── creative_juices_server.py  # FastMCP server with @mcp.tool() decorators
└── creative_juices_words.py   # Curated word lists by category
```

### Word List Philosophy

**Concrete over Abstract**: Uses tangible, everyday words to force metaphorical thinking rather than abstract concepts. This creates more creative distance and stronger metaphors.

**Categories**:
- **inspiring** (300+ words): Gentle, constructive actions + concrete objects
  - Human actions: painting, baking, melting, climbing, swimming
  - Animal behaviors: flying, burrowing, nesting, flocking, migrating
  - Everyday objects: shoe, door, window, chair, spoon
  - Natural elements: rain, river, tree, seed, leaf
  - Spans human history: primitive tools → ancient civilizations → modern industrial

- **out_of_the_box** (250+ words): Intense, dramatic actions + extreme concepts
  - Destructive: crushing, burning, drowning, exploding, rotting
  - Predatory: hunting, prowling, stalking, swarming
  - Sci-fi tech: hacking, encrypting, teleporting, cloaking, terraforming
  - Extreme objects: fire, storm, flood, venom, plasma-cutter, quantum-drive

### Elon Musk Frameworks

Four strategic thinking frameworks extracted from Musk's problem-solving approach:

1. **First Principles** (6 questions) - Challenge assumptions, find fundamental truths
2. **Limit Thinking** (6 questions) - Scale to extremes to find breaking points
3. **Platonic Ideal** (6 questions) - Start with perfect solution, work backwards
4. **Five-Step Optimization** (6 questions) - Question→Delete→Optimize→Accelerate→Automate

### Data Flow

**get_inspiration / think_outside_the_box**:
1. Tool called (no parameters)
2. Select 3 random verbs from category list
3. Select 3 random nouns from category list
4. Combine into verb-noun pairs
5. Return with contextual instruction

**reality_check**:
1. Tool called (no parameters)
2. Select 1 random question from each of 4 frameworks
3. Return questions with framework names and instruction

### Dependencies

- **FastMCP**: MCP server protocol implementation
- **Python stdlib random**: Random selection from word lists
- **No external APIs**: Fully local operation
- **No Django requirement**: Word lists are static Python dictionaries

### Error Handling

Current: None (Phase 2 enhancement)
- No try-except blocks
- No parameter validation
- No logging

## Configuration

- **Environment Variables**: None required
- **Optional Settings**: None
- **Default Behavior**: Works immediately with built-in word lists

## Installation & Running

```bash
# Run the MCP server
uv run python -m apps.ai.mcp.creative_juices_server

# Test locally with MCP Inspector
mcp-inspector uv run python -m apps.ai.mcp.creative_juices_server

# Run tests
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_creative_juices.py -v
```

## Usage Examples

### Scenario 1: Product Design Kickoff

**Tool**: `get_inspiration()`
**Response**:
```json
{
  "sparks": ["painting-shoe", "baking-door", "knitting-spoon"],
  "instruction": "Use these unexpected combinations as initial lenses:"
}
```
**Outcome**: "What if our app could 'paint' on a user's journey like painting a shoe?" → Leads to customization features

### Scenario 2: Breaking Through Analysis Paralysis

**Tool**: `think_outside_the_box()`
**Response**:
```json
{
  "sparks": ["crushing-fire", "exploding-storm", "swarming-venom"],
  "instruction": "Shatter your assumptions with these:"
}
```
**Outcome**: Dramatic metaphors force abandonment of incremental thinking

### Scenario 3: Validating Wild Ideas

**Tool**: `reality_check()`
**Response**:
```json
{
  "questions": [
    "What are the absolute truths here, known by physics?",
    "What happens at 1 unit vs 1 million units?",
    "What does the perfect version of this look like?",
    "Are your requirements dumb? Does this even matter?"
  ],
  "frameworks": ["first_principles", "limit_thinking", "platonic_ideal", "optimization"],
  "instruction": "Ground your thinking with one question from each Musk framework:"
}
```
**Outcome**: Strategic pressure-testing reveals fundamental constraints and opportunities

### Scenario 4: Three-Tool Creative Process

1. **Start**: `get_inspiration()` → Gentle concrete metaphors frame the problem
2. **Diverge**: `think_outside_the_box()` → Intense metaphors break linear thinking
3. **Converge**: `reality_check()` → Musk frameworks validate and refine ideas

## Word List Details

### Inspiring Category (VERBS)
- Human actions: painting, baking, melting, climbing, swimming, knitting...
- Animal (individual): flying, burrowing, nesting, molting, grazing...
- Animal (collective): flocking, herding, schooling, migrating, clustering...
- Mechanical/systematic: organizing, filing, sorting, measuring, calculating...
- Healing/restoration: healing, mending, repairing, cleaning, bandaging...
- Nurturing: cradling, rocking, feeding, nursing, wrapping...
- Primitive: knapping, chipping, flaking, thatching, foraging...
- Ancient civilization: casting, forging, plowing, sowing, quarrying...

### Inspiring Category (NOUNS)
- Everyday objects: shoe, door, window, chair, spoon, rope, mirror...
- Animal structures: nest, hive, web, shell, wing, feather...
- Natural elements: rain, river, tree, seed, leaf, rock...
- Comfort items: bed, quilt, cushion, tea, lamp...
- Primitive tools: hammerstone, pestle, hide, gourd, ember...
- Ancient items: ingot, anvil, plow, sickle, wheel, loom...

### Out-of-the-Box Category (VERBS)
- Destructive: crushing, burning, drowning, exploding, rotting, shattering...
- Predatory: hunting, prowling, stalking, swarming, devouring, ambushing...
- Defense: shielding, hiding, retreating, fortifying, armoring...
- Advanced tech: hacking, encrypting, compiling, rendering, overclocking...
- Sci-fi: teleporting, warping, cloaking, phasing, terraforming, ionizing...
- Biological sci-fi: mutating, evolving, metamorphosing, replicating, assimilating...
- Psychic: telepathizing, mind-melding, probing, dream-walking...

### Out-of-the-Box Category (NOUNS)
- Violent natural: fire, storm, flood, avalanche, earthquake, lightning...
- Predatory: venom, prey, predator, fang, claw, jaws, swarm...
- Wounds: blood, bone, wound, scar, ash, smoke...
- Sci-fi tech: datapad, neural-jack, plasma-cutter, quantum-drive, fusion-core...
- Spacecraft: stasis-pod, airlock, thruster, reactor, cryo-chamber...
- Primitive weapons: flint, handaxe, spearpoint, arrowhead, blade...
- Alien biology: spore, tentacle, chitin, pheromone, exoskeleton, bio-mass...
- Exotic materials: xenocrystal, plasma-silk, quantum-foam, dark-matter...

## Security Considerations

- **Data Handling**: No user data stored or transmitted
- **Permissions**: Read-only access to internal word lists
- **No External APIs**: Completely offline operation
- **No Authentication**: Stateless tools require no credentials
- **No Django Required**: Word lists are static Python code

## Design Decisions

### Why Concrete Words?

Abstract words like "crystallize-entropy" sound sophisticated but provide less creative friction. Concrete words like "baking-shoe" force genuine metaphorical thinking because the gap between concept and problem is larger.

### Why Three Separate Tools?

Different creative stages require different intensities:
- **Early stage**: Gentle nudges (inspiring)
- **Stuck stage**: Dramatic shocks (out-of-the-box)
- **Validation stage**: Strategic frameworks (reality_check)

### Why Musk Frameworks?

Proven strategic thinking patterns from a successful entrepreneur who's solved hard problems (reusable rockets, electric vehicles, brain-computer interfaces). These frameworks work.

### Why No Parameters?

Current implementation prioritizes simplicity. Phase 2 will add `count` and `intensity` parameters with Pydantic validation.

## Future Enhancements

### Phase 2 (Near-term)
- Parameter support: `get_inspiration(count=3, category="primitive")`
- Pydantic validation for type safety
- MCP prompts via `@mcp.prompt()` decorator
- Error handling and logging
- Return type consistency

### Phase 3 (Long-term)
- Expand word categories: nurturing, digital, biological
- Database-backed user word lists
- Session tracking and history
- Analytics: which combinations led to breakthroughs
- Integration with PydanticAI agents
- Claude Desktop package distribution

## Notes

- **Intentionally simple**: No config files, no database, no external APIs
- **Zero setup**: Works immediately with curated word lists
- **Historically grounded**: Word lists span human development (primitive → ancient → modern → sci-fi)
- **Battle-tested frameworks**: Musk's strategic patterns from real problem-solving
- **Concrete trumps abstract**: Tangible metaphors create stronger creative distance
