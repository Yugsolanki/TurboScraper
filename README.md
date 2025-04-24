# TurboScraper

## Overview

**TurboScraper** is a high-performance asynchronous web scraping toolkit designed for efficient and respectful website crawling and link extraction. It offers both a standalone Python library and a RESTful API interface, making it versatile for a wide range of scraping tasks. The project leverages modern asynchronous programming with `asyncio` and provides integration with Playwright for handling dynamic JavaScript-rendered content.

## Core Features

- **Asynchronous Architecture**: Built with Python's `asyncio` for maximum concurrency and efficiency
- **Dual Interface**: Use as a standalone Python library or interact via RESTful API
- **Playwright Integration**: Handles JavaScript-rendered content with browser automation
- **Advanced Configuration**:
  - Concurrent connection limits
  - Rate limiting with adaptive delays
  - Flexible retry logic with exponential backoff
  - Depth-limited crawling
  - Domain and path filtering (blacklist/whitelist)
- **Ethical Scraping**:
  - Optional `robots.txt` compliance
  - User-agent rotation
  - Rate limiting to respect server resources
- **Comprehensive Link Extraction**: Distinguishes between internal and external links

## Installation

### Prerequisites

- Python 3.13+
- Required dependencies:
  - aiohttp
  - asyncio
  - fastapi
  - playwright
  - robotsparser
  - validators

### Setup

```bash
# Clone the repository
git clone https://github.com/Yugsolanki/TurboScraper.git
cd TurboScraper

# Set up virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Install Playwright browsers
playwright install
```

## Usage

### Standalone Python Library

For direct integration into your Python projects:

```python
import asyncio
from standalone_scraper import AsyncParallelWebScraper

async def main():
    # Initialize scraper with custom settings
    scraper = AsyncParallelWebScraper(
        max_concurrent=10,
        max_depth=3,
        rate_limit_delay=1,
        respect_robots=False
    )

    # Setup
    await scraper.initialize()

    # Optional: Configure filters
    scraper.add_to_blacklist(
        domain=['blacklisteddomain.com'],
        path_patterns=[r'/private/.*', r'/admin/.*']
    )
    scraper.add_to_whitelist(
        path_patterns=[r'/public/.*', r'/products/.*']
    )

    # Start scraping
    scraped_links, external_links = await scraper.scrape_website(
        start_url="https://example.com/"
    )

    # Clean up
    await scraper.close()

    # Process results
    print(f"Found {len(scraped_links)} internal links")
    print(f"Found {len(external_links)} external links")

    # Save results
    with open("internal_links.txt", "w") as f:
        for link in scraped_links:
            f.write(f"{link}\n")

    with open("external_links.txt", "w") as f:
        for link in external_links:
            f.write(f"{link}\n")

if __name__ == "__main__":
    asyncio.run(main())
```

### RESTful API

TurboScraper provides a FastAPI-based REST interface for integration with any programming language or tool:

1. Start the API server:

```bash
uvicorn api:app --reload
```

2. Interact with the API endpoints:

#### Start a Scraping Job

```
POST /scrape
```

```json
{
  "max_concurrent": 10,
  "timeout": 10,
  "max_retries": 3,
  "max_depth": 3,
  "rate_limit_delay": 1.0,
  "respect_robots": false,
  "user_agents": [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36..."
  ],
  "start_url": "https://example.com",
  "whitelisted_domains": ["example.com"],
  "blacklisted_domains": [],
  "blacklisted_paths": ["/admin/.*"],
  "whitelisted_paths": []
}
```

Response:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "started",
  "message": "Scraping job started"
}
```

#### Check Job Status

```
GET /status
```

Response:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running"
}
```

#### Get Scraping Results

```
GET /results/?job_id=550e8400-e29b-41d4-a716-446655440000
```

Response (when completed):

```json
{
  "scraped_urls": ["https://example.com/page1", "https://example.com/page2", ...],
  "external_links": ["https://external-site.com/link1", ...],
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed"
}
```

#### Stop a Running Job

```
POST /stop
```

Response:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "stopped",
  "message": "Scraping job stopped"
}
```

## Configuration Options

### Scraper Settings

| Parameter          | Description                                         | Default          |
| ------------------ | --------------------------------------------------- | ---------------- |
| `max_concurrent`   | Maximum number of concurrent connections            | 10               |
| `timeout`          | Request timeout in seconds                          | 10               |
| `max_retries`      | Maximum number of retries for failed requests       | 3                |
| `max_depth`        | Maximum crawl depth                                 | 3                |
| `rate_limit_delay` | Delay between requests to the same domain (seconds) | 1.0              |
| `respect_robots`   | Whether to respect robots.txt rules                 | False            |
| `user_agents`      | List of user agents to rotate through               | [Mozilla/5.0...] |

### Domain and Path Filtering

- **Blacklisting**: Prevent scraping specific domains or path patterns

  ```python
  scraper.add_to_blacklist(
      domain=['example.com', 'private-site.com'],
      path_patterns=[r'/admin/.*', r'/private/.*']
  )
  ```

- **Whitelisting**: Only allow scraping specific domains or path patterns
  ```python
  scraper.add_to_whitelist(
      domain=['allowed-site.com'],
      path_patterns=[r'/public/.*', r'/products/.*']
  )
  ```

## Advanced Features

### Dynamic Content Handling

TurboScraper automatically detects when JavaScript rendering is needed and seamlessly switches between standard HTTP requests and browser-based scraping using Playwright.

### Adaptive Rate Limiting

The scraper monitors response times from each domain and adjusts request timing to avoid overwhelming target servers or triggering anti-scraping measures.

### Concurrent Processing

Task queuing and asynchronous processing allow efficient crawling of multiple pages simultaneously while respecting set limits.

## Technical Architecture

TurboScraper consists of two main components:

1. **Core Scraping Engine** (`standalone_scraper.py`):

   - Handles the actual web scraping logic
   - Manages concurrency, rate limiting, and retry mechanisms
   - Processes and filters links
   - Interfaces with browsers via Playwright

2. **API Layer** (`api.py` and `api_scraper.py`):
   - Provides RESTful endpoints using FastAPI
   - Manages job queuing and background processing
   - Exposes configuration options via API parameters
   - Returns structured results

## Development

### Requirements

- Python 3.13+
- Development tools: pytest, flake8, etc.

### Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues for bugs and feature requests.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgments

- The project uses [Playwright](https://playwright.dev) for browser automation
- [FastAPI](https://fastapi.tiangolo.com/) provides the API framework
- [aiohttp](https://docs.aiohttp.org/) powers the asynchronous HTTP requests

---

Happy Scraping! 🚀
