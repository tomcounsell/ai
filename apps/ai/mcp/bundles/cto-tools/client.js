#!/usr/bin/env node

/**
 * CTO Tools MCP Client - Node.js Proxy
 *
 * Forwards MCP protocol messages from stdin to the hosted service at
 * ai.yuda.me and returns responses to stdout.
 *
 * This allows users to use CTO Tools with zero local dependencies
 * since Node.js ships with Claude Desktop.
 */

const https = require('https');
const http = require('http');

const HOSTED_SERVICE_URL = 'https://ai.yuda.me/mcp/cto-tools/serve';

// Buffer for collecting stdin data
let stdinBuffer = '';

// Parse URL
const url = new URL(HOSTED_SERVICE_URL);
const client = url.protocol === 'https:' ? https : http;

/**
 * Forward a JSON-RPC message to the hosted service
 */
function forwardMessage(message) {
  const options = {
    hostname: url.hostname,
    port: url.port || (url.protocol === 'https:' ? 443 : 80),
    path: url.pathname,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(message)
    }
  };

  const req = client.request(options, (res) => {
    let responseData = '';

    res.on('data', (chunk) => {
      responseData += chunk;
    });

    res.on('end', () => {
      // Write response to stdout
      process.stdout.write(responseData + '\n');
    });
  });

  req.on('error', (error) => {
    console.error('Error forwarding message:', error, file=process.stderr);
    process.exit(1);
  });

  req.write(message);
  req.end();
}

// Read from stdin line by line
process.stdin.on('data', (chunk) => {
  stdinBuffer += chunk.toString();

  // Process complete lines
  let newlineIndex;
  while ((newlineIndex = stdinBuffer.indexOf('\n')) !== -1) {
    const line = stdinBuffer.substring(0, newlineIndex).trim();
    stdinBuffer = stdinBuffer.substring(newlineIndex + 1);

    if (line) {
      forwardMessage(line);
    }
  }
});

process.stdin.on('end', () => {
  // Process any remaining data
  if (stdinBuffer.trim()) {
    forwardMessage(stdinBuffer.trim());
  }
});

// Handle cleanup
process.on('SIGINT', () => {
  process.exit(0);
});

process.on('SIGTERM', () => {
  process.exit(0);
});
