# Creative Juices MCP Specification

## Overview

**Name**: Creative Juices MCP
**Purpose**: Provides randomness tools to encourage out-of-the-box thinking through concrete verb-noun combinations and strategic frameworks
**Key Value**: Break out of predictable AI responses by injecting unexpected tangible metaphors and proven strategic thinking frameworks

## Core Functionality

The server provides three tools designed for different stages of the creative process:

1. **`get_inspiration()`** - Early-stage creative framing with gentle, concrete metaphors
2. **`think_outside_the_box()`** - Mid-stage divergent thinking with intense, dramatic concepts
3. **`reality_check()`** - Grounding tool using Elon Musk's strategic thinking frameworks

## Tool Definitions

### get_inspiration

**Purpose**: Frame challenges in unexpected ways at the start of creative or problem-solving tasks

**Parameters**: None

**Returns**:
- `sparks`: Array of 3 verb-noun combinations (e.g., "painting-shoe", "baking-door", "knitting-spoon")
- `instruction`: Context-specific guidance for applying the metaphors

**When to Use**: Beginning creative work when you need unconventional starting points

**Word Strategy**: Uses "inspiring" category words - gentle, constructive actions paired with everyday concrete objects. Draws from human actions, animal behaviors, natural phenomena, and spans human history from primitive tools through ancient civilizations to modern industry.

### think_outside_the_box

**Purpose**: Force radical divergence when exploration has stalled or thinking has become too linear

**Parameters**: None

**Returns**:
- `sparks`: Array of 3 verb-noun combinations (e.g., "crushing-fire", "exploding-storm", "swarming-venom")
- `instruction`: Context-specific guidance for breaking assumptions

**When to Use**: Mid-conversation when convergent thinking needs disruption

**Word Strategy**: Uses "out_of_the_box" category words - intense, dramatic actions paired with extreme concepts. Includes destructive actions, predatory behaviors, sci-fi technology, and alien/futuristic elements.

### reality_check

**Purpose**: Ground creative thinking against strategic frameworks while maintaining openness

**Parameters**: None

**Returns**:
- `questions`: Array of 4 questions (one from each framework)
- `frameworks`: Array of framework names in same order as questions
- `instruction`: Context-specific guidance for strategic validation

**When to Use**: Validating assumptions, pressure-testing wild ideas, identifying what actually matters

**Framework Strategy**: Uses four Elon Musk thinking frameworks:
1. **First Principles** - Challenge assumptions, find fundamental truths (6 questions)
2. **Limit Thinking** - Scale to extremes to find breaking points (6 questions)
3. **Platonic Ideal** - Start with perfect solution, work backwards (6 questions)
4. **Five-Step Optimization** - Question→Delete→Optimize→Accelerate→Automate (6 questions)

Each call randomly selects one question from each framework to provide diverse strategic perspectives.

## Design Philosophy

### Concrete Over Abstract

The word lists use tangible, everyday words rather than abstract concepts. This is intentional:

- **Abstract words** (e.g., "crystallize-entropy") sound sophisticated but provide weak creative friction
- **Concrete words** (e.g., "baking-shoe") force genuine metaphorical thinking because the conceptual gap is larger

The larger the gap between the metaphor and the problem space, the stronger the creative reframing effect.

### Three-Stage Creative Process

Different creative stages require different intensities:

1. **Early stage**: Gentle nudges with familiar, constructive concepts (inspiring)
2. **Stuck stage**: Dramatic shocks with intense, disruptive concepts (out-of-the-box)
3. **Validation stage**: Strategic frameworks for reality-testing (Musk frameworks)

This mirrors natural creative problem-solving: diverge gently → diverge dramatically → converge strategically.

### Historical Dimension

Word lists intentionally span human development to maximize metaphorical range:
- Primitive era: knapping, flint, hammerstone, hide
- Ancient civilizations: forging, plow, anvil, wheel
- Modern industrial: organizing, filing, calculator, circuit
- Futuristic/sci-fi: teleporting, quantum-drive, plasma-cutter, bio-mass

This temporal diversity ensures metaphors can connect to any domain.

### Proven Strategic Frameworks

The Musk frameworks represent battle-tested patterns from solving hard real-world problems (reusable rockets, electric vehicles, brain-computer interfaces). They're not academic theory—they're practical strategic tools extracted from demonstrated success.

## Implementation Location

```
apps/ai/mcp/
├── creative_juices_server.py  # FastMCP server with three @mcp.tool() decorated functions
└── creative_juices_words.py   # Word lists organized by category (VERBS, NOUNS dictionaries)
```

**Implementation Note**: Refer to the latest MCP and FastMCP documentation for current patterns and best practices when implementing or modifying this server.

## Word List Categories

### Inspiring Category (300+ words)

**Verbs**: Human actions (painting, baking, melting, climbing, swimming, knitting), animal behaviors (flying, burrowing, nesting, flocking, migrating, herding), mechanical/systematic actions (organizing, filing, sorting, measuring, calculating), healing/restoration (mending, repairing, cleaning, bandaging), nurturing actions (cradling, rocking, feeding, nursing), primitive crafts (knapping, chipping, thatching, foraging), ancient skills (forging, plowing, sowing, quarrying)

**Nouns**: Everyday objects (shoe, door, window, chair, spoon, rope, mirror), animal structures (nest, hive, web, shell, wing, feather), natural elements (rain, river, tree, seed, leaf, rock), comfort items (bed, quilt, cushion, tea, lamp), primitive tools (hammerstone, pestle, hide, gourd, ember), ancient items (ingot, anvil, plow, sickle, wheel, loom)

### Out-of-the-Box Category (250+ words)

**Verbs**: Destructive actions (crushing, burning, drowning, exploding, rotting, shattering), predatory behaviors (hunting, prowling, stalking, swarming, devouring, ambushing), defense/evasion (shielding, hiding, retreating, fortifying, armoring), advanced technology (hacking, encrypting, compiling, rendering, overclocking), sci-fi actions (teleporting, warping, cloaking, phasing, terraforming, ionizing), biological sci-fi (mutating, evolving, metamorphosing, replicating, assimilating), psychic/mental (telepathizing, mind-melding, probing, dream-walking)

**Nouns**: Violent natural phenomena (fire, storm, flood, avalanche, earthquake, lightning), predatory elements (venom, prey, predator, fang, claw, jaws, swarm), wounds/damage (blood, bone, wound, scar, ash, smoke), sci-fi technology (datapad, neural-jack, plasma-cutter, quantum-drive, fusion-core), spacecraft elements (stasis-pod, airlock, thruster, reactor, cryo-chamber), primitive weapons (flint, handaxe, spearpoint, arrowhead, blade), alien biology (spore, tentacle, chitin, pheromone, exoskeleton, bio-mass), exotic materials (xenocrystal, plasma-silk, quantum-foam, dark-matter)

## Usage Examples

### Scenario 1: Product Design Kickoff

**Tool**: `get_inspiration()`
**Sample Output**:
```json
{
  "sparks": ["painting-shoe", "baking-door", "knitting-spoon"],
  "instruction": "Use these unexpected combinations as initial lenses:"
}
```
**Impact**: "What if our app could 'paint' on a user's journey like painting a shoe?" leads to thinking about customization, personalization, and user-driven aesthetic choices.

### Scenario 2: Breaking Through Analysis Paralysis

**Tool**: `think_outside_the_box()`
**Sample Output**:
```json
{
  "sparks": ["crushing-fire", "exploding-storm", "swarming-venom"],
  "instruction": "Shatter your assumptions with these:"
}
```
**Impact**: Dramatic, violent metaphors force abandonment of incremental thinking. "What if our approach could swarm like venom?" shifts from linear optimization to distributed, adaptive strategies.

### Scenario 3: Validating Wild Ideas

**Tool**: `reality_check()`
**Sample Output**:
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
**Impact**: Strategic pressure-testing reveals fundamental constraints and opportunities. First principles strip away bias, limit thinking exposes scaling issues, platonic ideal provides target state, optimization eliminates unnecessary work.

### Scenario 4: Complete Creative Process

A three-tool workflow demonstrates the full creative arc:

1. **Diverge (gentle)**: `get_inspiration()` → Concrete everyday metaphors frame the problem space
2. **Diverge (extreme)**: `think_outside_the_box()` → Intense dramatic metaphors break linear thinking patterns
3. **Converge (strategic)**: `reality_check()` → Musk frameworks validate and refine ideas against reality

This pattern supports natural creative problem-solving rhythms.

## Running the Server

### Local Development

```bash
# Start the MCP server locally
uv run python -m apps.ai.mcp.creative_juices_server

# Test locally with MCP Inspector
mcp-inspector uv run python -m apps.ai.mcp.creative_juices_server

# Run tests
uv run pytest apps/ai/tests/test_mcp_creative_juices.py -v
```

### Web Deployment

The Creative Juices MCP is deployed via Django at **https://ai.yuda.me/mcp/creative-juices**

**Available URLs**:
- Landing page: https://ai.yuda.me/mcp/creative-juices
- Manifest: https://ai.yuda.me/mcp/creative-juices/manifest.json
- README: https://ai.yuda.me/mcp/creative-juices/README.md

See [DEPLOYMENT.md](../../apps/ai/mcp/DEPLOYMENT.md) for full deployment details.

## Technical Characteristics

- **No external dependencies**: Uses Python stdlib `random` only
- **No authentication required**: Stateless tools with no credentials
- **No user data**: Nothing stored or transmitted
- **Fully local operation**: No external API calls
- **No configuration needed**: Works immediately with built-in word lists
- **No Django dependency**: Word lists are static Python dictionaries

## Design Rationale

### Why Not Parameters?

Current implementation has no parameters (count, intensity, categories) to keep the initial version simple and maximize adoption. Tools generate fixed outputs to reduce decision fatigue. Future versions may add parameters if usage patterns demonstrate clear value.

### Why Separate Tools Instead of One Tool with Parameters?

Three tools with clear names (`get_inspiration`, `think_outside_the_box`, `reality_check`) are more discoverable and self-explanatory than one tool with intensity/mode parameters. Tool names communicate intent and appropriate usage context.

### Why Musk Frameworks?

Other strategic frameworks exist (TRIZ, Six Thinking Hats, Design Thinking), but Musk's frameworks have demonstrated success on extremely hard engineering problems. They're practical and actionable rather than academic. The questions are direct and confrontational, which pairs well with the divergent thinking tools.

### Why Random Selection?

Randomness is the core value proposition. Predictable outputs would defeat the purpose of breaking habitual thinking patterns. Each tool call should feel fresh and unexpected.

## Security & Privacy

- **Read-only**: Tools only read from internal word lists
- **No data collection**: No logging, tracking, or analytics of tool usage
- **No external communication**: Completely offline operation
- **No authentication**: Appropriate for the risk level (generating random word pairs)
- **No injection risks**: Outputs are pre-curated word combinations only

## Success Metrics

Effectiveness of Creative Juices MCP can be evaluated through:

1. **Usage frequency**: Are users calling the tools regularly?
2. **Tool sequencing**: Are users following the three-stage pattern?
3. **Qualitative feedback**: Do users report creative breakthroughs?
4. **Integration patterns**: Are the tools being integrated into workflows/agents?

These metrics would require instrumentation not currently present in the implementation.
