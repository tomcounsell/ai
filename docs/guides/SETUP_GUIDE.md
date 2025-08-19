# Django Project Template: Developer Setup Guide

This guide provides detailed instructions for setting up the Django Project Template on your local development environment.

## Prerequisites

Before you begin, ensure you have the following installed on your machine:

- Python 3.9+ (recommend 3.12)
- Git
- PostgreSQL 14+
- Node.js 18+ and npm (for frontend assets)

## Automated Setup (Recommended)

The project includes an automated setup script that handles most of the setup process for you:

```bash
# Clone the main repository
git clone https://github.com/tomcounsell/django-project-template.git

# Change to the project directory
cd django-project-template

# Make the setup script executable
chmod +x setup_local_env.sh

# Run the setup script (with source to enable virtual environment activation)
source setup_local_env.sh
```

The setup script will:
1. Check prerequisites (Python, PostgreSQL, Node.js)
2. Set up a Python virtual environment
3. Install dependencies
4. Create configuration files
5. Set up the database
6. Run migrations
7. Prompt to create a superuser
8. Set up frontend dependencies

The script is idempotent, so it's safe to run multiple times. It tracks progress in a `.setup_progress` file to avoid repeating steps unnecessarily.

## Manual Setup

If you prefer to set up manually or need more control over the process, follow these steps:

### Step 1: Clone the Repository

```bash
# Clone the main repository
git clone https://github.com/tomcounsell/django-project-template.git

# Change to the project directory
cd django-project-template

# Optional: Add the original repository as upstream remote for future updates
git remote add upstream https://github.com/tomcounsell/django-project-template.git

# Verify remotes
git remote -v
```

Setting up the upstream remote allows you to fetch future improvements to the template:

```bash
# Fetch updates from the upstream repository
git fetch upstream

# View new commits on main branch
git log main..upstream/main

# Selectively apply specific improvements
git cherry-pick <commit-hash>
```

### Step 2: Set Up Python Environment and Dependencies

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On macOS/Linux
source venv/bin/activate
# On Windows
# venv\Scripts\activate

# Install uv (fast dependency installer)
pip install uv

# Install all project dependencies
./requirements/install.sh dev
```

### Step 3: Set Up Environment Variables

```bash
# Copy the local settings template
cp settings/local_template.py settings/local.py

# Copy the environment variables example file
cp .env.example .env.local

# Edit the file with your specific configuration
nano .env.local  # or use your preferred editor
```

Important environment variables to configure in your `.env.local` file:

- `DEBUG`: Set to `True` for development
- `SECRET_KEY`: A random string (50 characters) for crypto operations
- `DATABASE_URL`: PostgreSQL connection string (or DB_NAME, DB_USER, etc.)
- `DJANGO_SETTINGS_MODULE`: Set to `settings` for development
- AWS credentials (if using S3)
- Third-party integration keys (Loops, Supabase, etc.)
- Social authentication credentials

### Step 4: Database Setup

```bash
# Create a PostgreSQL database
createdb django_project_template

# Run migrations to set up the database schema
python manage.py migrate
```

### Step 5: Create Superuser

```bash
# Create an admin user
python manage.py createsuperuser
```

### Step 6: Install Pre-commit Hooks (Required)

```bash
# Install pre-commit hooks for code quality
uv run pre-commit install
uv run pre-commit install --hook-type pre-push

# Verify installation
uv run pre-commit --version

# Test the hooks
uv run pre-commit run --all-files
```

This step is **required** for all contributors to maintain code quality standards.

### Step 7: Run the Development Server

```bash
# Start the Django development server
python manage.py runserver
```

The site should now be accessible at `http://127.0.0.1:8000/`

### Step 7: Frontend Setup (if needed)

```bash
# Install frontend dependencies
npm install

# Run Tailwind CSS in watch mode
npm run watch:css
```

## Alternative: Using Docker

If you prefer to use Docker for development:

```bash
# Create a .env.local file from the example
cp .env.example .env.local

# Build and start the containers
docker-compose up -d

# Create a superuser
docker-compose exec web python manage.py createsuperuser
```

Visit http://127.0.0.1:8000/ to see your Docker-based application running.

## Common Development Tasks

### Running Tests

```bash
# Run all tests
DJANGO_SETTINGS_MODULE=settings pytest

# Run specific test file or module
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/test_user.py

# Run a specific test case
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/test_user.py::UserModelTestCase

# Run tests with coverage report
DJANGO_SETTINGS_MODULE=settings pytest --cov=apps --cov-report=html
```

### Creating Migrations

```bash
# Create new migrations for app changes
python manage.py makemigrations

# Apply migrations to database
python manage.py migrate

# Show migration status
python manage.py showmigrations
```

### Code Quality and Formatting

```bash
# Format your code with Black and isort
black .
isort .

# Run linting and type checking
flake8 .
mypy .
```

### Working with Git

```bash
# Create a new feature branch
git checkout -b feature/new-feature-name

# Add and commit changes
git add .
git commit -m "Add detailed description of changes"

# Push branch to remote
git push -u origin feature/new-feature-name

# Create a pull request (on GitHub website)
```

### Deployment

For deployments to platforms like Render or Heroku:

```bash
# Generate the requirements.txt file needed for deployment
./requirements/generate_deployment_requirements.sh

# This will create a requirements.txt file in the project root 
# which can be used by deployment platforms
```

## Project Structure Overview

### Key Directories

- `apps/`: Main application code, organized by domains
  - `common/`: Shared models, utilities, and behaviors
  - `public/`: User-facing views and templates
  - `api/`: REST API endpoints and serializers
  - `ai/`: AI-related functionality
- `settings/`: Project configuration
- `templates/`: Global templates
- `static/`: Static files (CSS, JS, images)
- `requirements/`: Dependency management

### Behavior Mixins

The project uses behavior mixins to add common functionality to models. For detailed examples of how to use behavior mixins, see the `BlogPost` model in `apps/common/models/blog_post.py`, which demonstrates all available behaviors in a real-world example.

## Troubleshooting

### Database Setup

After copying the local settings template in Step 3, you need to configure your database connection:

1. Edit your `settings/local.py` file
2. Set the database name to match your project name:
   ```python
   DATABASES = {
     'default': {
       'ENGINE': 'django.db.backends.postgresql',
       'NAME': 'django_project_template',  # Change this to your project name
       'USER': 'your_username',  # Your computer username
       'PASSWORD': '',  # Empty string for local development
       'HOST': 'localhost',
       'PORT': '5432',
     }
   }
   ```
3. Create the PostgreSQL database with your chosen name:
   ```bash
   createdb django_project_template  # Use the same name as in settings/local.py
   ```

### Missing Migrations

If you get a "no migrations to apply" message but the database doesn't have your models:
1. Check if your models are registered in the app's `__init__.py`
2. Run `python manage.py makemigrations <app_name>`
3. Check the generated migration file

### Test Failures

1. Make sure you're using the correct settings: `DJANGO_SETTINGS_MODULE=settings`
2. Check if dependencies are installed: `pip install -r requirements/dev.lock.txt`
3. Verify the test database can be created by your user

## Getting Help

If you need assistance:
1. Check the project documentation in the `docs/` directory
2. Look at the model implementation examples in `apps/common/models/`
3. Refer to the test cases for usage examples
4. Contact the project maintainers

## Next Steps

After setting up your development environment:
1. Review the architecture and code conventions in [CONTRIBUTING.md](CONTRIBUTING.md)
2. Explore the behavior mixins in `apps/common/behaviors/`
3. Look at existing models to understand patterns and conventions
4. Read up on HTMX integration for adding interactive frontend features
5. Check the [TODO.md](TODO.md) file to identify areas where you can contribute

### PyCharm Configuration

If you're using PyCharm as your IDE:

1. After setting up the project, ask Claude to help you configure the project in PyCharm
2. Point Claude to the [PYCHARM_CONFIG.MD](PYCHARM_CONFIG.MD) file
3. Claude can help you set up run configurations for:
   - Django Server
   - Running Tests
   - Code Linting
   - Tailwind CSS compilation
   - And more

This will create the necessary run configurations in your `.idea/` directory and make development with PyCharm more efficient.

### Project Customization and Branding

After setting up the basic environment, you should personalize the project with your own branding and naming. Use an AI assistant like Claude with the following prompt to help with this process:

```
# Project Customization Task: Rename Django Template to [YOUR_PROJECT_NAME]

Help me customize this Django project template for my new project called "[YOUR_PROJECT_NAME]".

## Tasks:
1. Create a fresh TODO.md file specific to my project (replace the template's TODO.md) that includes:
   - Major feature milestones for the MVP
   - Current development priorities in order
   - Known issues that need addressing
   - Code quality and testing improvements
   - Documentation needs
   - Integration requirements
   - Deployment plans (include specific steps for Render for web server and Redis, Neon for PostgreSQL)
   - Organize these into clear sections with target timelines where possible
2. Systematically rename all occurrences of "Django Project Template" and "Yudame" to "[YOUR_PROJECT_NAME]" across the codebase, including:
   - Project titles in HTML templates
   - README.md and documentation files
   - Settings files
   - Database configuration
   - Git repository references and URLs
   - Browser tab titles and meta descriptions

3. Update branding elements:
   - Page titles
   - Navigation menu items
   - Footer content
   - Copyright notices
   - Email templates and notification text
   - SEO metadata
   - Replace branding assets:
     - Replace the Yudame logo in `static/assets/img/logo-yudame.png` with your project logo
     - Update the favicon in `static/assets/favicon.png`
     - Replace any Yudame-specific images or icons
     - For temporary placeholders, suggest appropriate placeholder.co URLs (e.g., https://placehold.co/600x400/[HEX_COLOR]/[TEXT_COLOR]?text=[YOUR_PROJECT_NAME])

4. Prepare additional branding assets:
   - If a square favicon isn't provided, crop the main logo to create a square favicon
   - Optimize all image assets for web use (proper size and compression)

5. Update website navigation and footer:
   - Scrape the company's existing website for page links (Home, About, Features, etc.)
   - Add any relevant social media links from the company's online presence
   - Update the footer with company-specific content, links and contact information

6. Suggest a color scheme based on my project's domain and purpose

## Project Context:
- Project Name: "[YOUR_PROJECT_NAME]"
- Purpose: [Brief description of what your project does]
- Target Audience: [Who will use this product]
- Key Features: [Main functionality you plan to build]

Please provide a list of files changed, with specific edits made, and any follow-up tasks I should consider.
```

This will help establish your project's identity and remove all generic template references.

After completing the customization process, don't forget to:

1. Create and migrate the database with your new project name:
   ```bash
   # Create the database (if you haven't already)
   createdb your_project_name
   
   # Apply migrations to set up the database schema
   python manage.py migrate
   ```

2. Create a superuser to access the admin interface:
   ```bash
   python manage.py createsuperuser
   ```

3. Start the development server and verify your changes:
   ```bash
   python manage.py runserver
   ```

4. Navigate to http://127.0.0.1:8000/ in your browser to see your customized project.
