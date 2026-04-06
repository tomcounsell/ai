#!/usr/bin/env bash
# Happy path test: example-homepage
# Generated from trace. Do not edit manually.
# Source URL: https://example.com

set -euo pipefail

rodney open 'https://example.com'
rodney assert 'document.title === '\''Example Domain'\'''
rodney assert 'document.body.innerText.includes('\''This domain is for use in illustrative examples'\'')'
rodney assert 'document.querySelector('\''h1'\'') !== null'

# Final URL assertion
rodney assert 'window.location.href.includes('example.com')'

# Final text assertions
rodney assert 'document.body.innerText.includes('Example Domain')'

echo 'PASS: example-homepage'
