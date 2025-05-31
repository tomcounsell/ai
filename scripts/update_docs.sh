#!/bin/bash

# update_docs.sh - Systematically update documentation files using Claude Code CLI
# This script reviews each doc file individually and updates them to reflect codebase changes

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS_DIR="$PROJECT_ROOT/docs"

echo -e "${BLUE}🔄 Starting documentation update process...${NC}"
echo -e "${BLUE}Project root: $PROJECT_ROOT${NC}"
echo -e "${BLUE}Docs directory: $DOCS_DIR${NC}"

# Check if docs directory exists
if [[ ! -d "$DOCS_DIR" ]]; then
    echo -e "${RED}❌ Error: docs/ directory not found at $DOCS_DIR${NC}"
    exit 1
fi

# Check if claude command is available
if ! command -v claude &> /dev/null; then
    echo -e "${RED}❌ Error: 'claude' command not found. Please install Claude Code CLI.${NC}"
    exit 1
fi

# Function to update a single documentation file
update_doc_file() {
    local file_path="$1"
    local relative_path="${file_path#$PROJECT_ROOT/}"
    
    echo -e "\n${YELLOW}📝 Updating: $relative_path${NC}"
    
    # Create a comprehensive prompt for Claude Code
    local prompt="Review and update the documentation file $relative_path to ensure it accurately reflects the current codebase state.

Tasks to perform:
1. **Accuracy Check**: Verify all code references, file paths, function names, and architectural descriptions are current
2. **Cross-Reference Validation**: Ensure all references to other documentation files are correct and up-to-date
3. **Code Alignment**: Update any outdated patterns, examples, or architectural descriptions
4. **Recent Changes Integration**: Incorporate any recent codebase changes that affect this documentation
5. **Consistency**: Ensure terminology and architectural descriptions are consistent across docs

Key areas to focus on:
- File paths and directory structure references
- Function and class names mentioned in the documentation
- Architecture diagrams and system descriptions
- Cross-references to other documentation files
- Code examples and snippets
- API patterns and usage examples
- Recent changes to message handling, dev group logic, and Telegram integration

Please read the current documentation file and then:
1. Identify any outdated or incorrect information
2. Update the content to match the current codebase
3. Fix any broken cross-references to other docs
4. Ensure all examples and code references are accurate
5. Maintain the existing documentation style and structure

Only make changes that are necessary to improve accuracy and currency. Do not change the fundamental structure or purpose of the documentation unless there are significant architectural changes that require it."

    # Run Claude Code with the prompt
    if claude "$prompt"; then
        echo -e "${GREEN}✅ Successfully updated: $relative_path${NC}"
        return 0
    else
        echo -e "${RED}❌ Failed to update: $relative_path${NC}"
        return 1
    fi
}

# Get list of all markdown files in docs directory (including subdirectories)
echo -e "\n${BLUE}🔍 Finding documentation files...${NC}"
mapfile -t doc_files < <(find "$DOCS_DIR" -name "*.md" -type f | sort)

if [[ ${#doc_files[@]} -eq 0 ]]; then
    echo -e "${YELLOW}⚠️  No markdown files found in docs directory${NC}"
    exit 0
fi

echo -e "${BLUE}Found ${#doc_files[@]} documentation files:${NC}"
for file in "${doc_files[@]}"; do
    echo -e "  - ${file#$PROJECT_ROOT/}"
done

# Process each file sequentially
failed_files=()
successful_files=()

for doc_file in "${doc_files[@]}"; do
    if update_doc_file "$doc_file"; then
        successful_files+=("$doc_file")
    else
        failed_files+=("$doc_file")
    fi
    
    # Small delay between files to avoid overwhelming the system
    sleep 2
done

# Summary report
echo -e "\n${BLUE}📊 Documentation Update Summary${NC}"
echo -e "${GREEN}✅ Successfully updated: ${#successful_files[@]} files${NC}"

if [[ ${#successful_files[@]} -gt 0 ]]; then
    for file in "${successful_files[@]}"; do
        echo -e "  ✅ ${file#$PROJECT_ROOT/}"
    done
fi

if [[ ${#failed_files[@]} -gt 0 ]]; then
    echo -e "${RED}❌ Failed to update: ${#failed_files[@]} files${NC}"
    for file in "${failed_files[@]}"; do
        echo -e "  ❌ ${file#$PROJECT_ROOT/}"
    done
    echo -e "\n${YELLOW}⚠️  Please review failed files manually${NC}"
    exit 1
else
    echo -e "\n${GREEN}🎉 All documentation files updated successfully!${NC}"
fi

# Update root-level documentation files
echo -e "\n${BLUE}📄 Updating root-level documentation files...${NC}"

# Update README.md
echo -e "\n${YELLOW}📝 Updating: README.md${NC}"
if claude "Review and update the README.md file in the project root to ensure it accurately reflects the current project state.

Tasks to perform:
1. **Project Overview**: Update the project description to reflect current capabilities and architecture
2. **Installation & Setup**: Verify setup instructions match current requirements and scripts
3. **Usage Examples**: Update examples to reflect current agent system and tools
4. **Architecture Summary**: Ensure high-level architecture description is current
5. **Recent Features**: Include information about recent enhancements like intent classification, dev group logic, and message handling improvements
6. **Cross-References**: Ensure all references to documentation files in docs/ are accurate

Key recent changes to incorporate:
- Intent classification and message routing system
- Dev group logic with automatic response handling
- Enhanced Telegram message handling with reactions and read receipts
- MCP server integration and Claude Code compatibility
- Unified conversational development environment

Make the README welcoming and informative for new users while accurately representing the current system capabilities."; then
    echo -e "${GREEN}✅ Successfully updated: README.md${NC}"
    successful_files+=("README.md")
else
    echo -e "${RED}❌ Failed to update: README.md${NC}"
    failed_files+=("README.md")
fi

# Update CLAUDE.md
echo -e "\n${YELLOW}📝 Updating: CLAUDE.md${NC}"
if claude "Review and update the CLAUDE.md file in the project root to ensure it provides accurate guidance for Claude Code when working with this codebase.

Tasks to perform:
1. **Development Principles**: Verify principles still align with current development approach
2. **Architecture Overview**: Update to reflect current system architecture and recent changes
3. **Command References**: Ensure all scripts and commands are current and accurate
4. **File Structure**: Update directory structure descriptions to match current layout
5. **Integration Details**: Update information about MCP servers, tools, and agent capabilities
6. **Recent Changes**: Incorporate recent enhancements and architectural improvements

Key recent changes to incorporate:
- Intent classification system with message routing
- Enhanced dev group logic and automatic response handling  
- Reaction management system for Telegram messages
- Read receipt and message acknowledgment features
- Updated message handling patterns with better context extraction
- Improved cross-reference validation between documentation files

Ensure CLAUDE.md serves as an accurate and comprehensive guide for Claude Code when working with this project, reflecting the current state of the codebase and development patterns."; then
    echo -e "${GREEN}✅ Successfully updated: CLAUDE.md${NC}"
    successful_files+=("CLAUDE.md")
else
    echo -e "${RED}❌ Failed to update: CLAUDE.md${NC}"
    failed_files+=("CLAUDE.md")
fi

# Final cross-reference validation
echo -e "\n${BLUE}🔗 Running final cross-reference validation...${NC}"
claude "Please perform a final validation of all documentation files including docs/ directory, README.md, and CLAUDE.md. Check that:

1. All cross-references between documentation files are working correctly
2. File paths mentioned in docs match the actual project structure  
3. The documentation set is internally consistent across all files
4. All references to code files, functions, and architectural components are accurate
5. The documentation accurately reflects the current system architecture
6. README.md provides an accurate project overview that aligns with detailed docs
7. CLAUDE.md provides accurate guidance that reflects current development patterns

Focus on ensuring the entire documentation ecosystem works together as a cohesive whole. Report any remaining inconsistencies or broken references that need manual attention."

# Final summary report
echo -e "\n${BLUE}📊 Final Documentation Update Summary${NC}"
echo -e "${GREEN}✅ Successfully updated: ${#successful_files[@]} files total${NC}"

if [[ ${#successful_files[@]} -gt 0 ]]; then
    for file in "${successful_files[@]}"; do
        if [[ "$file" == *"/"* ]]; then
            echo -e "  ✅ ${file#$PROJECT_ROOT/}"
        else
            echo -e "  ✅ $file"
        fi
    done
fi

if [[ ${#failed_files[@]} -gt 0 ]]; then
    echo -e "${RED}❌ Failed to update: ${#failed_files[@]} files${NC}"
    for file in "${failed_files[@]}"; do
        if [[ "$file" == *"/"* ]]; then
            echo -e "  ❌ ${file#$PROJECT_ROOT/}"
        else
            echo -e "  ❌ $file"
        fi
    done
    echo -e "\n${YELLOW}⚠️  Please review failed files manually${NC}"
    exit 1
else
    echo -e "\n${GREEN}🎉 All documentation files updated successfully!${NC}"
fi

echo -e "\n${GREEN}🏁 Complete documentation update process finished!${NC}"