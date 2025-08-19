# Django Behavior Mixins Guide

This guide provides detailed explanations and example usage for each behavior mixin in the Django Project Template.

## What Are Behavior Mixins?

Behavior mixins are abstract Django model classes that encapsulate common functionalities that can be reused across different models. They follow the DRY (Don't Repeat Yourself) principle by providing reusable behaviors such as timestamps, authorship, publishing, etc.

## Available Behavior Mixins

The project includes the following behavior mixins, each serving a specific purpose:

### 1. Timestampable

**Location**: `apps/common/behaviors/timestampable.py`

**Purpose**: Tracks creation and modification timestamps for an object.

**Fields**:
- `created_at`: When the object was created
- `modified_at`: When the object was last modified

**Example Usage**:
```python
from django.db import models
from apps.common.behaviors import Timestampable

class Product(Timestampable, models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    
    # Now this model automatically has created_at and modified_at fields
```

### 2. Authorable

**Location**: `apps/common/behaviors/authorable.py`

**Purpose**: Associates content with an author and tracks authorship information.

**Fields**:
- `author`: Foreign key to the User model
- `is_author_anonymous`: Boolean flag for anonymous content
- `authored_at`: When the content was authored

**Properties**:
- `author_display_name`: Returns "Anonymous" or the author's name based on settings

**Example Usage**:
```python
from django.db import models
from apps.common.behaviors import Timestampable, Authorable

class Article(Timestampable, Authorable, models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    
    # Now you can access:
    # article.author, article.authored_at, article.is_author_anonymous
    # article.author_display_name
```

### 3. Publishable

**Location**: `apps/common/behaviors/publishable.py`

**Purpose**: Manages the publishing state of content with associated timestamps.

**Fields**:
- `published_at`: When the content was published
- `edited_at`: When the published content was edited
- `unpublished_at`: When the content was unpublished

**Properties**:
- `is_published`: Returns/sets publishing state

**Methods**:
- `publish()`: Publishes the content
- `unpublish()`: Unpublishes the content

**Example Usage**:
```python
from django.db import models
from apps.common.behaviors import Timestampable, Publishable

class NewsItem(Timestampable, Publishable, models.Model):
    headline = models.CharField(max_length=200)
    body = models.TextField()
    
    # Usage:
    # news_item.publish()  # Sets published_at to now
    # news_item.is_published  # Returns True if published
    # news_item.unpublish()  # Sets unpublished_at to now
```

### 4. Expirable

**Location**: `apps/common/behaviors/expirable.py`

**Purpose**: Adds expiration functionality to objects.

**Fields**:
- `valid_at`: When the object becomes valid
- `expired_at`: When the object expires

**Properties**:
- `is_expired`: Returns/sets expiration state

**Example Usage**:
```python
from django.db import models
from apps.common.behaviors import Timestampable, Expirable

class Promotion(Timestampable, Expirable, models.Model):
    name = models.CharField(max_length=100)
    discount_percent = models.PositiveIntegerField()
    
    # Usage:
    # promotion.is_expired = True  # Sets expired_at to now
    # promotion.is_expired  # Returns True if expired
```

### 5. Locatable

**Location**: `apps/common/behaviors/locatable.py`

**Purpose**: Associates objects with geographic information.

**Fields**:
- `address`: Foreign key to the Address model
- `longitude`: Longitude coordinate
- `latitude`: Latitude coordinate

**Example Usage**:
```python
from django.db import models
from apps.common.behaviors import Timestampable, Locatable

class Event(Timestampable, Locatable, models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()
    
    # Usage:
    # event.address = some_address
    # event.longitude = -122.4194
    # event.latitude = 37.7749
```

### 6. Permalinkable

**Location**: `apps/common/behaviors/permalinkable.py`

**Purpose**: Provides slug-based permalink functionality for SEO-friendly URLs.

**Fields**:
- `slug`: A URL-friendly identifier that can be used in URLs

**Methods**:
- `get_url_kwargs()`: Helper method for URL generation

**Additional Features**:
- Automatic slug generation from a `slug_source` property if available

**Example Usage**:
```python
from django.db import models
from apps.common.behaviors import Timestampable, Permalinkable

class Page(Timestampable, Permalinkable, models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    
    @property
    def slug_source(self):
        # This property is used to auto-generate the slug
        return self.title
    
    def get_absolute_url(self):
        return f"/pages/{self.slug}/"
```

### 7. Annotatable

**Location**: `apps/common/behaviors/annotatable.py`

**Purpose**: Allows attaching notes to objects.

**Fields**:
- `notes`: Many-to-many relationship with the Note model

**Properties**:
- `has_notes`: Returns True if the object has any notes

**Example Usage**:
```python
from django.db import models
from apps.common.behaviors import Timestampable, Annotatable

class Project(Timestampable, Annotatable, models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()
    
    # Usage:
    # project.notes.add(some_note)
    # project.has_notes  # Returns True if any notes exist
```

## Comprehensive Example: BlogPost Model

The `BlogPost` model in `apps/common/models/blog_post.py` demonstrates using all behavior mixins together to create a feature-rich content model:

```python
class BlogPost(
    Timestampable,
    Authorable,
    Publishable,
    Expirable,
    Locatable,
    Permalinkable,
    Annotatable,
    models.Model
):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField()
    # ... other fields
    
    @property
    def slug_source(self):
        return self.title
    
    # ... other properties and methods
```

This model includes:
- Creation and modification timestamps (Timestampable)
- Author association with anonymous option (Authorable)
- Publishing workflow with timestamps (Publishable)
- Expiration capabilities (Expirable)
- Geographic information (Locatable)
- SEO-friendly URLs (Permalinkable)
- Ability to attach notes (Annotatable)

## Best Practices

1. **Order of Inheritance**: Always place behavior mixins before `models.Model` in the class definition.

2. **Multiple Behaviors**: Combine mixins as needed, but be mindful of potential field conflicts.

3. **Overriding Methods**: You can override methods provided by mixins if needed, but try to maintain their original functionality.

4. **Always Test**: Ensure all behavior functionality is tested when used in a model.

5. **Documentation**: Document how your model uses each behavior mixin in the model's docstring.

## Creating New Behavior Mixins

When creating a new behavior mixin:

1. Create a file in `apps/common/behaviors/` following existing naming patterns
2. Inherit from `models.Model`
3. Include `abstract = True` in the Meta class
4. Add comprehensive docstrings explaining the behavior
5. Implement properties and methods as needed
6. Create unit tests for the behavior

Example structure for a new behavior mixin:

```python
from django.db import models

class NewBehavior(models.Model):
    """
    Docstring explaining the behavior's purpose and functionality.
    
    Attributes:
        field_one: Description
        field_two: Description
    
    Properties:
        property_name: Description
    
    Methods:
        method_name: Description
    """
    
    field_one = models.CharField(max_length=100)
    field_two = models.BooleanField(default=False)
    
    @property
    def property_name(self):
        # Implementation
        pass
    
    def method_name(self):
        # Implementation
        pass
    
    class Meta:
        abstract = True
```

## Testing Behavior Mixins

All behavior mixins are tested in `apps/common/tests/behaviors.py`, which contains:

1. **Database-backed tests**: Using Django's TestCase to test with ORM integration
2. **Direct tests**: Using Python's unittest with mocks to test without database

Both approaches provide comprehensive test coverage for all behaviors and verify fields, properties, and methods provided by the behavior mixin.

When testing a new behavior mixin, add both types of tests:

```python
# In apps/common/tests/behaviors.py

# Django TestCase-based test
class MyNewBehaviorTest(BehaviorTestMixin, TestCase):
    @property
    def model(self):
        return MyNewBehaviorModel  # Test model defined in the same file
    
    def test_some_property(self):
        obj = self.create_instance()
        # Test the behavior

# Direct unittest-based test
class TestMyNewBehaviorDirect(unittest.TestCase):
    def test_some_property(self):
        obj = mock.MagicMock(spec=MyNewBehavior)
        # Set up mocks
        # Test the behavior
```
