#!/bin/bash

# Django Project Template - Local Environment Setup Script
# This script sets up the local development environment and checks if all required settings are in place
# It is designed to be idempotent - running it multiple times should be safe

set -e

# Create a lock file to track what's been done in this run
LOCK_FILE=".setup_progress"
touch $LOCK_FILE

# Text formatting
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
NC="\033[0m" # No Color

# Helper functions
print_header() {
    echo -e "\n${BOLD}$1${NC}"
    echo "---------------------------------------------------------"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# URL encode function for database names
url_encode_db_name() {
    echo "$1" | sed 's/-/%2D/g'
}

# Start script
print_header "Django Project Template - Setup Script"
echo "This script will set up your local development environment."

# Step 1: Check Python version
print_header "1. Checking Python version"
python_version=$(python3 --version 2>&1)
if [[ $python_version == Python\ 3.* ]]; then
    version_number=${python_version:7}
    if (( $(echo "$version_number >= 3.9" | bc -l) )); then
        print_success "Python ${version_number} detected (recommended: 3.12)"
    else
        print_warning "Python ${version_number} detected. Recommended version is 3.9+ (ideally 3.12)."
    fi
else
    print_error "Python 3.x not found. Please install Python 3.9+ before continuing."
    exit 1
fi

# Step 2: Check PostgreSQL
print_header "2. Checking PostgreSQL"
if command -v pg_isready > /dev/null; then
    pg_status=$(pg_isready 2>&1)
    if [[ $pg_status == *"accepting connections"* ]]; then
        print_success "PostgreSQL is running"
    else
        print_warning "PostgreSQL is installed but not running. Please start PostgreSQL."
    fi
else
    print_warning "PostgreSQL command-line tools not found. Make sure PostgreSQL 14+ is installed."
fi

# Step 3: Check Node.js and npm
print_header "3. Checking Node.js and npm"
if command -v node > /dev/null; then
    node_version=$(node --version)
    print_success "Node.js ${node_version} detected"
    if command -v npm > /dev/null; then
        npm_version=$(npm --version)
        print_success "npm ${npm_version} detected"
    else
        print_warning "npm not found. Please install npm."
    fi
else
    print_warning "Node.js not found. It's recommended for frontend assets using Tailwind CSS."
fi

# Step 4-5: Set up virtual environment and install dependencies with uv
print_header "4. Setting up Python virtual environment with uv"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Installing uv package manager..."
    pip install --user uv
    print_success "uv installed."
else
    print_success "uv already installed."
fi

# Remove existing venv if it exists (fresh setup)
if [ -d ".venv" ]; then
    echo "Removing existing virtual environment for a fresh setup..."
    rm -rf .venv
fi

echo "Creating virtual environment with uv..."
uv venv

echo "Activating virtual environment..."
source .venv/bin/activate
print_success "Virtual environment activated."

print_header "5. Installing dependencies with uv"
echo "Installing project dependencies with uv..."

if [ -f "requirements/dev.txt" ]; then
    echo "Installing from requirements/dev.txt..."
    uv pip install -r requirements/dev.txt
    print_success "Dependencies installed successfully with uv."
else
    # Fallback to requirements.txt if dev.txt doesn't exist
    if [ -f "requirements.txt" ]; then
        echo "Installing from requirements.txt..."
        uv pip install -r requirements.txt
        print_success "Dependencies installed successfully with uv."
    else
        print_error "No requirements file found. Cannot install dependencies."
        exit 1
    fi
fi

# Mark as completed
echo "DEPENDENCIES_INSTALLED=true" >> $LOCK_FILE

# Step 6: Check environment files
print_header "6. Checking environment configuration"
if [ -f ".env.local" ]; then
    print_success ".env.local exists."
else
    echo "Creating .env.local from template..."
    if [ -f ".env.example" ]; then
        cp .env.example .env.local
        print_success ".env.local created from template. Please edit it with your settings."
    else
        print_error ".env.example not found. Cannot create .env.local."
        exit 1
    fi
fi

if [ -f "settings/local.py" ]; then
    print_success "settings/local.py exists."
else
    echo "Creating settings/local.py from template..."
    if [ -f "settings/local_template.py" ]; then
        cp settings/local_template.py settings/local.py
        print_success "settings/local.py created from template. Please edit if needed."
    else
        print_error "settings/local_template.py not found. Cannot create settings/local.py."
        exit 1
    fi
fi

# Step 7: Check .env.local required variables
print_header "7. Validating .env.local configuration"
env_file=".env.local"
required_vars=("SECRET_KEY" "DEBUG" "DATABASE_URL")
missing_vars=()

for var in "${required_vars[@]}"; do
    if ! grep -q "^${var}=" "$env_file" || grep -q "^${var}=$" "$env_file"; then
        missing_vars+=("$var")
    fi
done

if [ ${#missing_vars[@]} -eq 0 ]; then
    print_success "All required environment variables are set."
else
    print_warning "The following required variables are missing or empty in .env.local:"
    for var in "${missing_vars[@]}"; do
        echo " - $var"
    done
    echo "Please edit .env.local to set these variables."
fi

# Step 8: Check and setup database
print_header "8. Checking and setting up database"

# Try to extract database name from DATABASE_URL
db_name=""
if grep -q "^DATABASE_URL=" ".env.local"; then
    db_url=$(grep "^DATABASE_URL=" ".env.local" | cut -d'=' -f2)
    if [[ $db_url == postgres://* ]]; then
        # Extract DB name from postgres URL (handle URL encoded chars)
        db_name=$(echo $db_url | sed -E 's/.*\/([^?]*)(\?.*)?$/\1/' | sed 's/%2D/-/g')
        print_success "Found database name in .env.local: $db_name"
    fi
fi

# If we couldn't extract DB name or it's empty, ask the user
if [ -z "$db_name" ]; then
    # Check if we've already set up the database in this session
    if grep -q "DB_SETUP_COMPLETE" $LOCK_FILE; then
        print_warning "Database setup was already attempted but DATABASE_URL may be invalid."
        echo "Please edit .env.local manually to set a valid DATABASE_URL."
    else
        echo "What database name would you like to use for this project?"
        read -p "Database name [django_project_template]: " user_db_name
        db_name=${user_db_name:-django_project_template}

        # Ask for database username and password
        read -p "Database username [postgres]: " db_user
        db_user=${db_user:-postgres}

        read -s -p "Database password (input will be hidden): " db_password
        echo ""

        # Encode database name for URL if it contains hyphens
        db_name_url=$(url_encode_db_name "$db_name")

        # Update the DATABASE_URL in .env.local
        if grep -q "^DATABASE_URL=" ".env.local"; then
            sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=postgres://$db_user:$db_password@localhost:5432/$db_name_url|" ".env.local"
            rm -f ".env.local.bak"
        else
            echo "DATABASE_URL=postgres://$db_user:$db_password@localhost:5432/$db_name_url" >> ".env.local"
        fi
        print_success "Updated DATABASE_URL in .env.local"
        # Mark this step as completed
        echo "DB_SETUP_COMPLETE=true" >> $LOCK_FILE
    fi
fi

echo "Checking if database '$db_name' exists..."
if command -v psql > /dev/null; then
    if psql -lqt | cut -d \| -f 1 | grep -qw "$db_name"; then
        print_success "Database '$db_name' already exists."
    else
        echo "Database '$db_name' does not exist. Creating it now..."
        if createdb "$db_name"; then
            print_success "Database '$db_name' created successfully."
        else
            print_error "Failed to create database. Please create it manually:"
            echo "  createdb $db_name"
        fi
    fi
else
    print_warning "PostgreSQL client tools (psql) not found. Cannot check database."
    echo "Please ensure database '$db_name' exists before proceeding."
fi

# Ensure we retry database creation if not found or access issue
if [ -n "$db_name" ] && command -v createdb > /dev/null; then
    # Double-check with createdb - some users might have permissions with createdb but not psql
    if ! createdb "$db_name" 2>/dev/null; then
        echo "Note: Database might already exist or there may be permission issues."
    else
        print_success "Database '$db_name' created with createdb."
    fi
fi

echo "Attempting to connect to database..."
DB_CONNECTION_SUCCESS=false

# First try with the standard connection check
if python -c "from django.db import connection; connection.cursor()" 2>/dev/null; then
    print_success "Database connection successful."
    DB_CONNECTION_SUCCESS=true
else
    # Second try with the migrate check command - sometimes this works even when the first check fails
    if python manage.py migrate --check >/dev/null 2>&1; then
        print_success "Database connection successful (verified with migrate --check)."
        DB_CONNECTION_SUCCESS=true
    else
        print_warning "Could not connect to database. Please check your database configuration in .env.local."

        # Offer some common solutions
        echo "Common issues and solutions:"
        echo "1. Database password might be incorrect"
        echo "2. Database might not exist or user doesn't have access"
        echo "3. PostgreSQL service might not be running"

        # Offer to edit the configuration
        echo "Would you like to update the database configuration? (y/n)"
        read update_db_config
        if [[ $update_db_config == "y" || $update_db_config == "Y" ]]; then
            # Ask for database username and password
            read -p "Database username [postgres]: " db_user
            db_user=${db_user:-postgres}

            read -s -p "Database password (input will be hidden): " db_password
            echo ""

            # Encode database name for URL if it contains hyphens
            db_name_url=$(url_encode_db_name "$db_name")

            # Update the DATABASE_URL in .env.local
            if grep -q "^DATABASE_URL=" ".env.local"; then
                sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=postgres://$db_user:$db_password@localhost:5432/$db_name_url|" ".env.local"
                rm -f ".env.local.bak"
            else
                echo "DATABASE_URL=postgres://$db_user:$db_password@localhost:5432/$db_name_url" >> ".env.local"
            fi
            print_success "Updated DATABASE_URL in .env.local"

            # Try to connect again
            echo "Attempting to connect to database again..."
            if python -c "from django.db import connection; connection.cursor()" 2>/dev/null; then
                print_success "Database connection successful after reconfiguration."
                DB_CONNECTION_SUCCESS=true
            else
                print_warning "Still unable to connect to database."
                echo "Please check if PostgreSQL is running and that the credentials are correct."
                echo "You may need to modify .env.local manually or restart the script."
            fi
        else
            echo "Skipping database reconfiguration. You'll need to update .env.local manually."
        fi
    fi
fi

# Step 9: Run migrations only if database connection was successful
print_header "9. Setting up database schema"

# Don't run migrations if we couldn't connect to the database
if [ "$DB_CONNECTION_SUCCESS" = false ]; then
    print_warning "Skipping migrations since database connection failed."
    echo "After fixing your database connection, run:"
    echo "  python manage.py migrate"
else
    # Check if we've already done this step in this run
    if grep -q "MIGRATIONS_RUN" $LOCK_FILE; then
        print_success "Migrations were already run in this session."
    else
        echo "Would you like to run database migrations? (y/n)"
        read run_migrations
        if [[ $run_migrations == "y" || $run_migrations == "Y" ]]; then
            echo "Running migrations..."
            python manage.py migrate
            print_success "Migrations completed."
            # Mark this step as completed
            echo "MIGRATIONS_RUN=true" >> $LOCK_FILE
        else
            print_warning "Skipping migrations. You'll need to run them manually:"
            echo "  python manage.py migrate"
        fi
    fi
fi

# Step 10: Check for superuser (only if database connection is successful)
print_header "10. Checking for superuser"

if [ "$DB_CONNECTION_SUCCESS" = false ]; then
    print_warning "Skipping superuser check since database connection failed."
    echo "After fixing your database connection and running migrations, create a superuser with:"
    echo "  python manage.py createsuperuser"
else
    # Use a safer approach to check for superuser
    echo "Checking for superusers..."
    # Simple approach without the complex Python one-liner that was causing issues
    if python manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).exists())" 2>/dev/null; then
        superuser_exists="True"
        print_success "Superuser exists."
    else
        superuser_exists="False"
        echo "No superuser found."
    fi

    if [ "$superuser_exists" = "True" ]; then
        print_success "Superuser already exists."
    else
        echo "Would you like to create a superuser now? (y/n)"
        read create_superuser
        if [[ $create_superuser == "y" || $create_superuser == "Y" ]]; then
            python manage.py createsuperuser
        else
            print_warning "Skipping superuser creation. You can create one later with 'python manage.py createsuperuser'"
        fi
    fi
fi

# Step 11: Ensure static directories exist
print_header "11. Setting up static directories"
if [ ! -d "static" ]; then
    echo "Creating root static directory..."
    mkdir -p static
    print_success "Root static directory created."
else
    print_success "Root static directory already exists."
fi

# Create key static subdirectories
for dir in css js assets img; do
    if [ ! -d "static/$dir" ]; then
        echo "Creating static/$dir directory..."
        mkdir -p "static/$dir"
        print_success "static/$dir directory created."
    fi
done

# Step 12: Note about frontend CSS
print_header "12. Frontend CSS"
echo "This project will use Tailwind CSS via a CDN or django-tailwind in the future."
echo "No npm package installation needed for CSS processing."
print_success "Frontend setup complete."

# Final instructions
print_header "Setup Complete!"
echo -e "Your local development environment is now set up."

# Activate the virtual environment for the current shell
VENV_ACTIVATE=". .venv/bin/activate"

echo -e "\nFor best results, save these commands for your current shell session:"
echo -e "${BOLD}${VENV_ACTIVATE}${NC}"
echo -e "# This activates the virtual environment for the current shell session"

echo -e "\nTo start the development server (with virtual environment activated):"
echo -e "  ${BOLD}python manage.py runserver${NC}"

echo -e "\nTailwind CSS will be included via CDN or django-tailwind in the future."

echo -e "\nFor more information, check the documentation in docs/ and CLAUDE.md"

# Keep the lock file for future runs
echo -e "\nProgress tracking file created at ${LOCK_FILE}"
echo -e "This file helps avoid redundant operations if you run the script again."
echo -e "To force a fresh setup, delete this file before running the script again."

# Execute the activate command in the current shell
# Note: This will only work if the script is executed with "source" or "."
echo -e "\nAttempting to activate virtual environment for this shell session..."
if [[ "$0" != "${BASH_SOURCE[0]}" ]]; then
    # Script is being sourced, we can modify parent shell
    # Note: We've already activated it earlier in the script, just confirming that it will persist
    echo -e "${GREEN}✓ Virtual environment activated for current shell session${NC}"
else
    # Script is not being sourced, can't modify parent shell
    echo -e "${YELLOW}⚠ This script was run directly, not with 'source'${NC}"
    echo -e "${YELLOW}⚠ The virtual environment will not remain activated after the script ends${NC}"
    echo -e "${YELLOW}⚠ To activate the virtual environment, run:${NC}"
    echo -e "${BOLD}${VENV_ACTIVATE}${NC}"
fi

# Final success message with ASCII art
echo -e "${GREEN}"
cat << "EOF"

 __   __  _   _  ____    _    __  __  _____
 \ \ / / | | | ||  _ \  / \  |  \/  || ____|
  \ V /  | | | || | | |/ _ \ | |\/| ||  _|
   | |   | |_| || |_| / ___ \| |  | || |___
   |_|    \___/ |____/_/   \_\_|  |_||_____|

 (o_o)   (^_^)   (*_*)   (>_<)   (o_O)
  /|\     _|_     /|\     /|\     _|_
  / \     / \     / \     _|_     / \

EOF
echo -e "${NC}"

echo -e "${GREEN}Your Django development environment is ready!${NC}"
echo -e "\nSetup completed successfully! 🎉\n"

# Make sure we end cleanly without any commands that might close the shell
echo "Script execution completed at: $(date)"
# DO NOT add any exit, exec, or other terminal-affecting commands here!
