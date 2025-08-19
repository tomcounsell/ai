# Django Model Conventions

This document outlines the conventions and best practices for models in this Django project. Following these guidelines ensures consistency, maintainability, and best practices throughout the codebase.

## General Model Structure

### 1. Inheritance and Behaviors
- Models inherit from `models.Model`
- Common behaviors are implemented as mixins (e.g., `Timestampable`)
- Abstract base classes are used for shared functionality (e.g., `AbstractUser`)

### 2. Field Definitions
- Fields are grouped logically
- Field options are consistently ordered: `max_length`, `null`, `blank`, `default`, other options
- Proper use of `null=True` and `blank=True` based on field type
  - CharField/TextField: Use `blank=True`, default=""
  - Other fields: Use both `null=True, blank=True` when optional

### 3. Model Organization
The standard model structure follows this order:
1. Field definitions
2. Model properties (using `@property` decorator)
3. Model methods
4. Meta class (if needed)
5. Related forms (in same file)

## Naming Conventions

### Fields
- Use lowercase with underscores (snake_case)
- Boolean fields should start with `is_` or `has_`
- Date/time fields should end with `_at` (e.g., `activated_at`, `reviewed_at`)
- Foreign keys should use the model name in singular form and reference with a string (e.g., `"app.Author"`, `"common.User"`)
- Related names should be plural (e.g., `related_name="addresses"`)

### Properties and Methods
- Use descriptive names that indicate the return value
- Property names should be nouns (e.g., `inline_string`, `serialized`)
- Boolean properties should start with `is_` or `has_`
- Methods should use verb phrases (e.g., `get_absolute_url()`)

## Documentation

### 1. Docstrings
- Complex models should have docstrings explaining their purpose
- Include key attributes and properties in the docstring
- Document any special behaviors or important notes
- Example:
  ```python
  class Upload(models.Model):
      """
      A model representing an uploaded file, including its metadata and properties.

      Attributes:
          original (str): The original URL of the uploaded file
          name (str): The name of the file
          ...
      """
  ```

### 2. Properties
- Properties should be used for computed values
- Complex properties should include type hints
- Properties that require heavy computation should be cached if frequently accessed

## Behavior Mixins

The project uses abstract behavior mixins to encapsulate common model behaviors. These are located in `apps/common/behaviors/`.

### Available Behaviors

- `Timestampable`: Adds `created_at` and `modified_at` fields with automatic updates
- `Publishable`: Manages content publishing workflow with `is_published`, `published_at`, and `unpublished_at`
- `Authorable`: Tracks content authors with anonymous option (`author`, `authored_at`, `is_author_anonymous`)
- `Locatable`: Adds location data with address and coordinate fields
- `Permalinkable`: Manages URL slugs and permalink generation with `slug` field
- `Expirable`: Handles content expiration with `expired_at` field and validity tracking
- `Annotatable`: Provides notes relationship management for adding annotations to models

For detailed usage examples of these behaviors, see the `BlogPost` model in `apps/common/models/blog_post.py`, which demonstrates all available behaviors in a real-world example.

### Behavior Implementation Guidelines

1. **Abstract Base Classes**
   - All behaviors must inherit from `models.Model`
   - Must include `abstract = True` in Meta class
   - Should focus on a single responsibility

2. **Documentation**
   - Include comprehensive docstrings
   - List all fields and their purposes
   - Document any properties or methods
   - Include usage examples if complex

3. **Field Naming**
   - Use consistent suffixes:
     - Timestamps end with `_at` (e.g., `created_at`, `published_at`)
     - Boolean flags start with `is_` (e.g., `is_published`, `is_author_anonymous`)
     - Foreign keys use singular form (e.g., `author`)

4. **Properties and Methods**
   - Implement property getters and setters for boolean states
       - especially when the state is defined by a datetime field (eg. `is_expired` for `expired_at < now()`)
   - Include helper methods for common operations
   - Use clear, action-oriented names for methods (e.g., `publish()`, `unpublish()`)

5. **Related Names**
   - Use `%(class)s` for dynamic related names
   - This allows the same behavior to be used in multiple models

### Usage Example

```python
class MyModel(Timestampable, Publishable, models.Model):
    # Your model fields here
    pass
```

## Best Practices

### 1. Model Methods
- Override `__str__` method for human-readable representation
- Implement `serialized` property for API responses
- Keep model methods focused on model-specific logic
- Use proper error handling in model methods
- Use type hints for all methods and properties
- Follow verb phrases for methods, nouns for properties

### 2. Field Choices
- Use appropriate field types for the data
- Set reasonable field lengths for CharFields
- Use JSONField for flexible schema data
- Implement proper on_delete behavior for foreign keys

### 3. Security and Privacy
- Never store sensitive information in plain text
- Use proper field types for sensitive data (e.g., encrypted fields)
- Implement proper access controls at the model level

### 4. Performance
- Index fields used in frequent lookups
- Use select_related() and prefetch_related() for related field queries
- Consider adding db_index=True for frequently queried fields

## Forms
- Forms related to a model should be defined in a separate file in the `apps/<app_name>/forms/` directory
- Use ModelForm when possible
- Explicitly specify fields in Meta class
- Add appropriate widgets and validation

## Testing
- Each model should have corresponding test cases in `apps/<app_name>/tests/test_models/`
- Test edge cases and validation
- Include tests for model methods and properties
- Test database constraints and unique fields
- Use factory classes in `apps/common/tests/factories.py` to create test instances
- Test behavior mixins in `apps/common/tests/behaviors.py` with both database-backed and direct approaches
- Aim for 100% test coverage for models and behavior mixins
- For more details on testing behavior mixins, see [BEHAVIOR_MIXINS.md](BEHAVIOR_MIXINS.md#testing-behavior-mixins)

## Migrations
- Keep migrations focused and atomic
- Review migration files before committing
- Test migrations, especially for large data sets
- Document any manual steps required for migrations

## Version Control
- Include meaningful commit messages for model changes
- Document breaking changes in model structure
- Keep track of deprecated fields and methods

Remember to follow these conventions when creating or modifying models to maintain consistency across the project.
