# Sphinx Documentation

This directory contains the Sphinx documentation for the Django Project Template.

## Building the Documentation

### Automated Build

Run the build script from the project root:

```bash
./docs/scripts/build_docs.sh
```

### Manual Build

From the project root directory:

1. Generate API documentation:

```bash
cd docs/sphinx_docs && sphinx-apidoc -o source/api ../../apps --separate --force && cd -
```

2. Build the HTML documentation:

```bash
cd docs/sphinx_docs && make html && cd -
```

3. View the documentation:

```bash
open docs/sphinx_docs/build/html/index.html
```

## Hosting on GitHub Pages

The documentation is automatically built and deployed to GitHub Pages whenever changes are pushed to the main branch. This is handled by a GitHub Actions workflow defined in `.github/workflows/docs.yml`.

### Accessing the Documentation

The documentation is available at: `https://[username].github.io/[repository-name]/`

### Manual Trigger

You can manually trigger the documentation build and deployment process from the GitHub Actions tab by selecting the "Documentation" workflow and clicking "Run workflow".

## Documentation Structure

- `source/`: Documentation source files
  - `conf.py`: Sphinx configuration
  - `index.rst`: Main index page
  - `_static/`: Static files (CSS, images)
  - `_templates/`: Custom templates
  - `apps/`: App-specific documentation
  - `models/`: Model documentation
  - `views/`: View documentation
  - `api/`: API documentation

## Writing Documentation

- Use reStructuredText (`.rst`) format for most documentation
- Follow the Google docstring style for Python code
- Include type hints in your code for better API documentation
- Use cross-references to link between documentation sections
