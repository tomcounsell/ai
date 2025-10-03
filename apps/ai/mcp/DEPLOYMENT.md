# Deploying Creative Juices MCP to mcp.yuda.me

## Files to Serve

### Landing Page
- **File**: `creative_juices_web.html`
- **URL**: https://mcp.yuda.me/creative-juices (or https://mcp.yuda.me/creative-juices.html)
- **Purpose**: Public-facing information and installation instructions

### Manifest
- **File**: `creative_juices_manifest.json`
- **URL**: https://mcp.yuda.me/creative-juices/manifest.json
- **Purpose**: Machine-readable metadata for MCP registries and discovery

### README (Optional)
- **File**: `CREATIVE_JUICES_README.md`
- **URL**: https://mcp.yuda.me/creative-juices/README.md
- **Purpose**: Markdown version for GitHub-style rendering or API documentation

## Web Server Configuration

### Option 1: Static File Hosting

If using nginx, Apache, or similar:

```nginx
# nginx example
server {
    listen 80;
    server_name mcp.yuda.me;

    root /var/www/mcp;
    index index.html;

    location /creative-juices {
        try_files $uri $uri.html $uri/ =404;
    }

    location /creative-juices/manifest.json {
        add_header Content-Type application/json;
        add_header Access-Control-Allow-Origin *;
    }
}
```

File structure:
```
/var/www/mcp/
└── creative-juices/
    ├── index.html              (copy of creative_juices_web.html)
    ├── manifest.json           (copy of creative_juices_manifest.json)
    └── README.md               (copy of CREATIVE_JUICES_README.md)
```

### Option 2: GitHub Pages

1. Create a `gh-pages` branch
2. Copy files to root or subdirectory
3. Enable GitHub Pages in repository settings
4. Point custom domain mcp.yuda.me to GitHub Pages

### Option 3: Cloudflare Pages / Vercel / Netlify

1. Connect repository to hosting service
2. Set build directory to `apps/ai/mcp/`
3. Configure custom domain mcp.yuda.me
4. Deploy automatically on push

## DNS Configuration

Point your domain to the web server:

```
# If using static hosting
mcp.yuda.me.  A      YOUR.SERVER.IP.ADDRESS
mcp.yuda.me.  AAAA   YOUR:IPV6:ADDRESS

# If using GitHub Pages/Cloudflare/etc
mcp.yuda.me.  CNAME  your-username.github.io.
```

## CORS Headers (Important for API access)

Ensure the manifest.json is served with appropriate CORS headers:

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, OPTIONS
Access-Control-Allow-Headers: Content-Type
```

## SSL/TLS Certificate

Use Let's Encrypt or your hosting provider's SSL:

```bash
# Certbot example for nginx
sudo certbot --nginx -d mcp.yuda.me
```

## Testing Deployment

Once deployed, verify:

1. **Landing page loads**: Visit https://mcp.yuda.me/creative-juices
2. **Manifest accessible**: `curl https://mcp.yuda.me/creative-juices/manifest.json`
3. **CORS headers present**: Check browser dev tools or `curl -I`
4. **SSL valid**: No certificate warnings

## Maintenance

### Updating Content

When updating the MCP:

1. Edit source files in `apps/ai/mcp/`
2. Rebuild/redeploy to mcp.yuda.me
3. Test installation instructions still work
4. Update version number in manifest.json

### Monitoring

Set up monitoring for:
- Uptime (pingdom, uptime robot, etc.)
- SSL certificate expiration
- Traffic/usage analytics (optional, privacy-friendly)

## Future Enhancements

### MCP Registry Submission

Once stable, submit to MCP registries:
- Include manifest.json URL
- Reference landing page for human-readable docs
- Tag with appropriate categories

### Analytics (Optional)

If desired, add privacy-friendly analytics:
- Plausible Analytics
- Simple Analytics
- Self-hosted Umami

Update HTML with tracking code (respecting GDPR/privacy).

### API Endpoint (Future)

Consider adding a simple API endpoint:

```
GET /creative-juices/api/random-spark
Returns: {"verb": "painting", "noun": "shoe"}
```

This would allow web-based demos without running the full MCP server.

## Quick Deploy Script

```bash
#!/bin/bash
# deploy.sh - Quick deployment to web server

# Build directory
BUILD_DIR="build/creative-juices"
mkdir -p $BUILD_DIR

# Copy files
cp apps/ai/mcp/creative_juices_web.html $BUILD_DIR/index.html
cp apps/ai/mcp/creative_juices_manifest.json $BUILD_DIR/manifest.json
cp apps/ai/mcp/CREATIVE_JUICES_README.md $BUILD_DIR/README.md

# Deploy to server (adjust for your setup)
rsync -avz $BUILD_DIR/ user@mcp.yuda.me:/var/www/mcp/creative-juices/

echo "Deployed to https://mcp.yuda.me/creative-juices"
```

Make executable: `chmod +x deploy.sh`

## Notes

- HTML file is self-contained (no external dependencies)
- Manifest uses standard MCP format for discovery
- All URLs should use HTTPS in production
- Consider adding sitemap.xml for SEO if desired
