# Creative Juices MCP

**Break free from predictable AI responses with randomized creative prompts**

Creative Juices is an MCP (Model Context Protocol) server that provides three tools designed to help you think outside the box. It generates random verb-noun combinations and strategic questions to push LLMs into divergent thinking patterns.

## What It Does

Creative Juices provides three tools for different stages of creative problem-solving:

1. **`get_inspiration`** - Gentle creative nudges with everyday metaphors (e.g., "painting-shoe", "knitting-spoon")
2. **`think_outside_the_box`** - Intense creative shocks with dramatic metaphors (e.g., "crushing-fire", "swarming-venom")
3. **`reality_check`** - Strategic validation using Elon Musk's thinking frameworks (First Principles, Limit Thinking, Platonic Ideal, Five-Step Optimization)

### Why Use This?

- **Break habitual thinking**: Random concrete metaphors force your brain into new pathways
- **No setup required**: Zero configuration, works immediately
- **Completely private**: No external APIs, no data collection, runs entirely locally
- **Battle-tested frameworks**: Strategic questions from proven problem-solving patterns

## Quick Start

### Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) package manager (recommended) or pip

### Installation

#### Option 1: From Source (Current)

```bash
# Clone the repository
git clone https://github.com/tomcounsell/cuttlefish.git
cd cuttlefish

# Install dependencies
uv sync --all-extras

# Run the server
uv run python -m apps.ai.mcp.creative_juices_server
```

#### Option 2: Direct Installation (Future)

```bash
# When published as standalone package
uvx creative-juices-mcp
```

### Test the Server

```bash
# Run with MCP Inspector for interactive testing
npx @modelcontextprotocol/inspector uv run python -m apps.ai.mcp.creative_juices_server
```

## Claude Desktop Configuration

Add to your Claude Desktop configuration file:

### macOS
`~/Library/Application Support/Claude/claude_desktop_config.json`

### Windows
`%APPDATA%\Claude\claude_desktop_config.json`

### Configuration

```json
{
  "mcpServers": {
    "creative-juices": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/cuttlefish",
        "run",
        "python",
        "-m",
        "apps.ai.mcp.creative_juices_server"
      ]
    }
  }
}
```

**Important**: Replace `/ABSOLUTE/PATH/TO/cuttlefish` with the actual path to your cloned repository.

After adding the configuration:
1. Restart Claude Desktop completely (Cmd+Q on macOS, close from system tray on Windows)
2. Look for the 🔌 icon in the bottom-right corner
3. Creative Juices tools should appear in the available tools list

## Usage Examples

### Example 1: Product Design Kickoff

```
You: I'm designing a new todo app. Help me think creatively about it.
Assistant: [calls get_inspiration]
Creative Juices: {"sparks": ["painting-shoe", "baking-door", "knitting-spoon"], ...}
Assistant: Interesting! What if your app could "paint" on a user's journey like painting a shoe?
          This makes me think about personalization and letting users customize their experience...
```

### Example 2: Breaking Through Writer's Block

```
You: I'm stuck on how to make this sci-fi story more interesting.
Assistant: [calls think_outside_the_box]
Creative Juices: {"sparks": ["teleporting-plasma", "mutating-void", "phasing-tentacle"], ...}
Assistant: What if your characters could phase through dimensional boundaries like a tentacle?
          This could introduce non-linear storytelling where scenes overlap across realities...
```

### Example 3: Validating a Business Idea

```
You: Is my SaaS idea actually viable?
Assistant: [calls reality_check]
Creative Juices: {
  "questions": [
    "What are the absolute truths here, known by physics?",
    "What happens at 1 unit vs 1 million units?",
    "What does the perfect version of this look like?",
    "Are your requirements dumb? Does this even matter?"
  ],
  "frameworks": ["first_principles", "limit_thinking", "platonic_ideal", "optimization"]
}
Assistant: Let's pressure-test this with these strategic questions...
```

## Tool Reference

### `get_inspiration()`

**When to use**: Start of creative work, brainstorming, initial problem framing

**Returns**:
- 3 gentle verb-noun combinations from everyday life
- Instruction text for applying the metaphors

**Word categories**: Human actions, animal behaviors, natural phenomena, primitive tools, ancient crafts

### `think_outside_the_box()`

**When to use**: Mid-process when stuck, need to break linear thinking, analysis paralysis

**Returns**:
- 3 intense verb-noun combinations with dramatic imagery
- Instruction text for shattering assumptions

**Word categories**: Destructive actions, predatory behaviors, sci-fi technology, alien biology

### `reality_check()`

**When to use**: Validating ideas, pressure-testing solutions, grounding creative concepts

**Returns**:
- 4 strategic questions (one from each Musk framework)
- Framework names for each question
- Instruction text for strategic validation

**Frameworks**:
- **First Principles**: Challenge assumptions, find fundamental truths
- **Limit Thinking**: Scale to extremes to find breaking points
- **Platonic Ideal**: Start with perfect solution, work backwards
- **Five-Step Optimization**: Question → Delete → Optimize → Accelerate → Automate

## Design Philosophy

### Why Concrete Words?

Abstract words like "crystallize-entropy" sound impressive but create weak creative friction. Concrete words like "baking-shoe" force genuine metaphorical thinking because the conceptual gap is larger.

The larger the gap between the metaphor and your problem, the stronger the creative reframing effect.

### Why Three Tools?

Different creative stages need different intensities:
- **Early stage**: Gentle nudges (inspiring)
- **Stuck stage**: Dramatic shocks (out-of-the-box)
- **Validation stage**: Strategic frameworks (reality check)

Use them in sequence for a complete creative process: diverge gently → diverge dramatically → converge strategically.

## Development

### Running Tests

```bash
uv run pytest apps/ai/tests/test_mcp_creative_juices.py -v
```

### Code Location

```
apps/ai/mcp/
├── creative_juices_server.py  # Main MCP server implementation
└── creative_juices_words.py   # Curated word lists (600+ words)
```

### Adding New Words

Edit `creative_juices_words.py`:
- `VERBS["inspiring"]` - Gentle, constructive actions
- `VERBS["out_of_the_box"]` - Intense, dramatic actions
- `NOUNS["inspiring"]` - Everyday concrete objects
- `NOUNS["out_of_the_box"]` - Extreme, dramatic concepts

Guidelines:
- Prefer concrete over abstract
- Use simple, everyday language
- Span human history (primitive → ancient → modern → futuristic)
- Balance constructive and destructive imagery

## Troubleshooting

### Server won't start
```bash
# Check Python version (need 3.11+)
python --version

# Reinstall dependencies
uv sync --all-extras

# Try running directly
uv run python -m apps.ai.mcp.creative_juices_server
```

### Tools not appearing in Claude Desktop
1. Verify configuration path is absolute, not relative
2. Check JSON syntax in config file (use a JSON validator)
3. Restart Claude Desktop completely (Cmd+Q, not just close window)
4. Check Claude Desktop logs:
   - macOS: `~/Library/Logs/Claude/`
   - Windows: `%APPDATA%\Claude\logs\`

### Words feel repetitive
This is normal with small sample sizes. The server has 300+ inspiring words and 250+ out-of-the-box words. Run the tools multiple times to see variety.

## Technical Details

- **No external dependencies**: Uses Python stdlib `random` only
- **No authentication**: Stateless tools, no credentials needed
- **No data collection**: Zero logging, tracking, or analytics
- **Fully offline**: No external API calls
- **No configuration**: Works immediately with built-in word lists

## Contributing

Contributions welcome! Areas for improvement:

- **Word lists**: Add more words, new categories, cultural diversity
- **Frameworks**: Additional strategic thinking frameworks beyond Musk
- **Parameters**: Support for count/intensity/category selection
- **Localization**: Translations for non-English creative work

## License

Part of the [Cuttlefish](https://github.com/tomcounsell/cuttlefish) project.

## Credits

- **Elon Musk frameworks**: Extracted from public talks and interviews
- **Word curation**: Designed for maximum creative distance
- **MCP protocol**: Built with [FastMCP](https://github.com/jlowin/fastmcp)

## Links

- **Full specification**: See `docs/specs/CREATIVE_JUICES_MCP.md` for design philosophy
- **Web interface**: https://mcp.yuda.me/creative-juices (coming soon)
- **Issues/feedback**: [GitHub Issues](https://github.com/tomcounsell/cuttlefish/issues)

---

**Break free from the predictable. Let randomness be your creative fuel.**
