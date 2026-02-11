#!/bin/bash

# Script to create report.html and transcript.html viewers for all episodes

for episode_dir in /Users/valorengels/src/cuttlefish/apps/podcast/pending-episodes/*/*; do
  if [ ! -d "$episode_dir" ]; then
    continue
  fi

  # Check if report.md exists
  if [ ! -f "$episode_dir/report.md" ]; then
    continue
  fi

  # Find transcript JSON file
  transcript_file=$(ls "$episode_dir"/*_transcript.json 2>/dev/null | head -1)
  if [ -z "$transcript_file" ]; then
    continue
  fi

  transcript_basename=$(basename "$transcript_file")

  # Get series name for back link
  series_dir=$(dirname "$episode_dir")
  series_name=$(basename "$series_dir")

  # Determine back link text based on series
  case "$series_name" in
    "active-recovery")
      back_text="Active Recovery Series"
      ;;
    "cardiovascular-health")
      back_text="Cardiovascular Health Series"
      ;;
    "kindergarten-first-principles")
      back_text="Kindergarten First Principles Series"
      ;;
    "solomon-islands-telecom-series")
      back_text="Solomon Islands Telecom Series"
      ;;
    "stablecoin-series")
      back_text="Stablecoin Series"
      ;;
    *)
      back_text="Series Index"
      ;;
  esac

  echo "Creating viewers for: $episode_dir"

  # Create report.html
  cat > "$episode_dir/report.html" << 'REPORT_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Episode Report</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.5.1/github-markdown.min.css">
    <style>
        body {
            background: #ffffff;
            padding: 0;
            margin: 0;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            padding: 2rem;
        }
        .markdown-body {
            box-sizing: border-box;
            min-width: 200px;
            max-width: 980px;
            margin: 0 auto;
            padding: 45px;
        }
        .back-link {
            display: inline-block;
            margin-bottom: 2rem;
            padding: 0.5rem 1rem;
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            color: #24292f;
            text-decoration: none;
            border-radius: 6px;
            font-size: 14px;
            transition: all 0.2s;
        }
        .back-link:hover {
            background: #24292f;
            color: #ffffff;
            border-color: #24292f;
        }
        @media (max-width: 767px) {
            .markdown-body {
                padding: 15px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="../index.html" class="back-link">← Back to BACK_TEXT_PLACEHOLDER</a>
        <article class="markdown-body" id="content">
            Loading...
        </article>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script>
        // Try XMLHttpRequest for better local file support
        const xhr = new XMLHttpRequest();
        xhr.open('GET', 'report.md', true);
        xhr.onload = function() {
            if (xhr.status === 200 || xhr.status === 0) { // 0 for local files
                document.getElementById('content').innerHTML = marked.parse(xhr.responseText);
            } else {
                document.getElementById('content').innerHTML =
                    '<p>Error loading report (status: ' + xhr.status + ')</p>';
            }
        };
        xhr.onerror = function() {
            document.getElementById('content').innerHTML =
                '<p>Error loading report. If viewing locally, try opening via: <code>python3 -m http.server 8000</code> and navigate to <code>http://localhost:8000</code></p>';
        };
        xhr.send();
    </script>
</body>
</html>
REPORT_EOF

  # Replace back link placeholder
  sed -i '' "s/BACK_TEXT_PLACEHOLDER/$back_text/g" "$episode_dir/report.html"

  # Create transcript.html
  cat > "$episode_dir/transcript.html" << 'TRANSCRIPT_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Episode Transcript</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            line-height: 1.6;
            color: #333;
            background: #fff;
            padding: 1rem;
            margin: 0;
            max-width: 900px;
            margin: 0 auto;
        }
        .back-link {
            display: inline-block;
            margin-bottom: 1rem;
            color: #666;
            text-decoration: none;
            font-size: 0.9rem;
        }
        .back-link:hover {
            color: #000;
        }
        h1 {
            font-size: 1.5rem;
            font-weight: 400;
            margin: 0 0 1.5rem 0;
            border-bottom: 1px solid #e0e0e0;
            padding-bottom: 0.5rem;
        }
        .metadata {
            font-size: 0.85rem;
            color: #666;
            margin-bottom: 1.5rem;
            padding: 0.75rem;
            background: #f8f8f8;
            border-left: 3px solid #ddd;
        }
        .segment {
            margin-bottom: 0.75rem;
            font-size: 0.95rem;
        }
        .timestamp {
            display: inline-block;
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 0.8rem;
            color: #999;
            margin-right: 0.5rem;
        }
        .text {
            display: inline;
        }
        .loading {
            padding: 2rem;
            color: #999;
        }
    </style>
</head>
<body>
    <a href="../index.html" class="back-link">← Back to BACK_TEXT_PLACEHOLDER</a>
    <h1>Episode Transcript</h1>
    <div id="content" class="loading">Loading transcript...</div>

    <script>
        function formatTime(seconds) {
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${mins}:${secs.toString().padStart(2, '0')}`;
        }

        // Try XMLHttpRequest for better local file support
        const xhr = new XMLHttpRequest();
        xhr.open('GET', 'TRANSCRIPT_FILENAME_PLACEHOLDER', true);
        xhr.onload = function() {
            if (xhr.status === 200 || xhr.status === 0) { // 0 for local files
                try {
                    const data = JSON.parse(xhr.responseText);
                    const container = document.getElementById('content');

                    // Display metadata
                    let html = '<div class="metadata">';
                    html += `Duration: ${formatTime(data.duration)} | `;
                    html += `Language: ${data.language} | `;
                    html += `Segments: ${data.segments.length}`;
                    html += '</div>';

                    // Display segments - compact format
                    data.segments.forEach((segment, index) => {
                        html += '<div class="segment">';
                        html += `<span class="timestamp">${formatTime(segment.start)}</span>`;
                        html += `<span class="text">${segment.text.trim()}</span>`;
                        html += '</div>';
                    });

                    container.innerHTML = html;
                } catch (error) {
                    document.getElementById('content').innerHTML =
                        '<p>Error parsing transcript: ' + error.message + '</p>';
                }
            } else {
                document.getElementById('content').innerHTML =
                    '<p>Error loading transcript (status: ' + xhr.status + ')</p>';
            }
        };
        xhr.onerror = function() {
            document.getElementById('content').innerHTML =
                '<p>Error loading transcript. If viewing locally, try: <code>python3 -m http.server 8000</code></p>';
        };
        xhr.send();
    </script>
</body>
</html>
TRANSCRIPT_EOF

  # Replace placeholders
  sed -i '' "s/BACK_TEXT_PLACEHOLDER/$back_text/g" "$episode_dir/transcript.html"
  sed -i '' "s/TRANSCRIPT_FILENAME_PLACEHOLDER/$transcript_basename/g" "$episode_dir/transcript.html"

done

echo "Done! Created viewers for all episodes."
