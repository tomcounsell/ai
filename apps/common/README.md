# Common App

## Purpose

The Common app serves as the foundation for the entire project, providing core models, utilities, and behaviors that are used across all other apps. It implements the shared functionality and data structures that maintain consistency throughout the application.

## Features

### Behavior Mixins

- **Timestampable**: Adds creation and modification timestamps to models
- **Authorable**: Handles authorship and attribution 
- **Publishable**: Manages publishing state and timestamps
- **Expirable**: Manages expiration states
- **Locatable**: Adds location-related fields
- **Permalinkable**: Provides permalink functionality
- **Annotatable**: Allows for annotations

### Core Models

- **User**: Extended Django user model with additional profile information
- **Address**: Geographic address with street, city, state, postal code and country
- **Country**: Countries with names, codes and calling codes
- **Currency**: Currency definitions with codes, names and symbols
- **City**: City data with name and country association
- **Document**: Document management with metadata
- **Image**: Image management with dimensions and metadata
- **Upload**: File upload tracking with type, size and URL
- **Note**: Text notes with authorship
- **Team**: Team organization with user roles and permissions

### Utilities

- **Database**: Database helpers, custom model fields
- **Data**: S3, SQS, and other data processing utilities
- **Processing**: Multithreading, text processing, serialization
- **Compression**: Image compression utilities
- **Django**: Custom Django middleware and backends
- **Email**: Email sending utilities
- **Logger**: Structured logging

## Usage

The common app should be used for any functionality that:

1. Is needed across multiple apps
2. Provides core data models for the application
3. Extends Django's built-in functionality
4. Implements shared business logic

## Development Guidelines

- All models should follow the behavior mixin pattern when appropriate
- Follow naming conventions in [/docs/MODEL_CONVENTIONS.md](/docs/MODEL_CONVENTIONS.md)
- Write thorough tests for all models and utilities
