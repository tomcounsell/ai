# AWS S3 Integration

This module provides integration with Amazon S3 for file uploads, storage, and retrieval.

## Features

- Direct browser uploads to S3 using pre-signed URLs
- Upload management with Django ORM via the Upload model
- File retrieval with optional pre-signed URLs
- Seamless integration with the Django project

## Configuration

Add the following settings to your environment:

```python
# In settings/third_party.py
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', "")
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', "")
AWS_S3_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', "")
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
```

And in your .env file:

```
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_STORAGE_BUCKET_NAME=your-bucket-name
AWS_REGION=us-east-1
```

### S3 Bucket Configuration

Your S3 bucket needs to be configured to allow direct uploads from browsers. At minimum, your bucket needs:

1. CORS configuration:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<CORSConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<CORSRule>
    <AllowedOrigin>*</AllowedOrigin>
    <AllowedMethod>GET</AllowedMethod>
    <AllowedMethod>POST</AllowedMethod>
    <AllowedMethod>PUT</AllowedMethod>
    <MaxAgeSeconds>3000</MaxAgeSeconds>
    <AllowedHeader>*</AllowedHeader>
</CORSRule>
</CORSConfiguration>
```

2. Bucket policy that allows public read (if your uploads need to be publicly accessible):
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadGetObject",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::your-bucket-name/*"
        }
    ]
}
```

## Usage

### Direct Browser Uploads

To enable direct browser uploads, use the following pattern:

```python
from apps.integration.aws.shortcuts import get_direct_upload_form_data

# Generate form data for the upload
result = get_direct_upload_form_data(
    original_filename="example.jpg",
    content_type="image/jpeg",
    max_file_size=5 * 1024 * 1024,  # 5MB
    prefix="uploads/images"
)

if result["success"]:
    # Return the form data to the frontend
    form_url = result["form_url"]
    form_fields = result["form_fields"]
    upload_id = result["upload_id"]
    
    # Frontend will use this to build an HTML form that POSTs directly to S3
    # After successful upload, mark the upload as complete
    complete_result = complete_upload(upload_id, status=Upload.STATUS_COMPLETE)
```

### Frontend Implementation Example

```html
<form action="{{ form_url }}" method="post" enctype="multipart/form-data">
    {% for name, value in form_fields.items %}
    <input type="hidden" name="{{ name }}" value="{{ value }}">
    {% endfor %}
    <input type="file" name="file">
    <button type="submit">Upload</button>
</form>
```

### Retrieving Uploaded Files

```python
from apps.integration.aws.shortcuts import get_upload_file_url

# Get a URL for an uploaded file
result = get_upload_file_url(upload_id, presigned=True, expiration=3600)

if result["success"]:
    file_url = result["url"]
    content_type = result["content_type"]
    file_name = result["name"]
```

### Deleting Uploads

```python
from apps.integration.aws.shortcuts import delete_upload

# Delete an uploaded file
result = delete_upload(upload_id)

if result["success"]:
    # File was deleted from S3 and the Upload record was removed
    pass
```

## Using the S3Client Directly

For more advanced use cases, you can use the S3Client directly:

```python
from apps.integration.aws.s3 import S3Client

client = S3Client()

# Upload a file
result = client.upload_file(
    file_path="/path/to/local/file.txt",
    object_key="uploads/file.txt",
    public=True
)

# List objects in a prefix
result = client.list_objects(prefix="uploads/", max_keys=100)

# Delete an object
result = client.delete_object(object_key="uploads/file.txt")
```

## Security Considerations

- Never expose AWS credentials in your frontend code
- Always set a reasonable max_file_size in get_direct_upload_form_data
- Use pre-signed URLs with short expiration times for sensitive files
- Consider implementing server-side validation of file types and content

## Testing

Tests for the S3 integration are available in the `tests/` directory. To run the tests:

```bash
DJANGO_SETTINGS_MODULE=settings pytest apps/integration/aws/tests
```
