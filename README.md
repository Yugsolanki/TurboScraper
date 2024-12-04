# AsyncParallelWebScraper

## Overview

`AsyncParallelWebScraper` is an asynchronous web scraper designed to efficiently crawl websites by leveraging concurrency and respecting robots.txt rules. This tool is built using `aiohttp` for asynchronous HTTP requests, `playwright` for browser-based scraping, and `BeautifulSoup` for HTML parsing.

## Features

- **Asynchronous Processing:** Utilizes `asyncio` to handle multiple concurrent requests efficiently.
- **Rate Limiting:** Respects rate limits for each domain to avoid being blocked.
- **Robots.txt Compliance:** Optionally respects `robots.txt` rules to ensure ethical scraping.
- **Retry Logic:** Implements retry logic for failed requests with exponential backoff.
- **Blacklist/Whitelist Support:** Allows blacklisting and whitelisting of domains and path patterns.
- **Playwright Integration:** Uses `playwright` for browser-based scraping when necessary.

## Installation

To install the required dependencies, use the following command:

```bash
pip install aiohttp playwright beautifulsoup4 validators
```

## Usage

### Basic Usage

To start scraping a website, you can use the following code snippet:

```python
import asyncio
from scraper import AsyncParallelWebScraper

async def main():
    start_url = "https://crawler-test.com/"
    blacklisted_domains = ['blacklisteddomain.com'] # Optional or pass []
    blacklisted_paths = [r'/blacklisted/path.*', r'/another/blacklisted/path.*'] # Optional or pass []
    whitelisted_paths = [r'/whitelisted/path.*', r'/another/whitelisted/path.*'] # Optional or pass []

    scraper = AsyncParallelWebScraper(max_concurrent=10, max_depth=3, rate_limit_delay=1, respect_robots=False)
    await scraper.initialize()
    scraper.add_to_blacklist(domain=blacklisted_domains, path_patterns=blacklisted_paths)
    scraper.add_to_whitelist(path_patterns=whitelisted_paths)

    start_time = time.time()
    scraped_links, external_links = await scraper.scrape_website(start_url,
                                                                 blacklisted_domains=blacklisted_domains,
                                                                 blacklisted_paths=blacklisted_paths,
                                                                 whitelisted_paths=whitelisted_paths)
    end_time = time.time()
    await scraper.close()

    print(f"\nScraped Links: {len(scraped_links)}")
    print(f"External Links: {len(external_links)}")
    print(f"Time taken: {end_time - start_time:.2f} seconds")

    with open("scraped_links.txt", "w", encoding='utf-8') as file:
        for link in scraped_links:
            file.write(f"{link}\n")
    with open("external_links.txt", "w", encoding='utf-8') as file:
        for link in external_links:
            file.write(f"{link}\n")

if __name__ == "__main__":
    asyncio.run(main())
```

### Configuration Options

- **max_concurrent:** Maximum number of concurrent connections.
- **timeout:** Timeout for each request.
- **max_retries:** Maximum number of retries for failed requests.
- **max_depth:** Maximum depth for URL crawling.
- **rate_limit_delay:** Delay between requests to the same domain.
- **respect_robots:** Whether to respect `robots.txt` rules.
- **user_agents:** List of user agents to rotate.

### Blacklist/Whitelist

You can add domains and path patterns to the blacklist and whitelist using the following methods:

```python
scraper.add_to_blacklist(domain=['example.com'], path_patterns=[r'/private.*'])
scraper.add_to_whitelist(path_patterns=[r'/public.*'])
```

## Documentation

### Class: `AsyncParallelWebScraper`

#### Methods

- **`__init__(self, max_concurrent=10, timeout=10, max_retries=3, max_depth=3, rate_limit_delay=1, respect_robots=True, user_agents=['MyScraper/1.0 (contact@example.com)'])`**
  - Initialize the scraper with specified parameters.

- **`add_to_blacklist(self, domain=None, paths=None, path_patterns=None)`**
  - Add domains and path patterns to the blacklist.

- **`add_to_whitelist(self, path_patterns=None)`**
  - Add path patterns to the whitelist.

- **`initialize(self)`**
  - Initialize aiohttp session and playwright browser.

- **`close(self)`**
  - Close aiohttp session and playwright browser.

- **`get_headers(self)`**
  - Get headers with rotated User-Agent.

- **`get_robots_parser(self, domain)`**
  - Retrieve or fetch the robots.txt parser for a given domain.

- **`is_allowed(self, url, user_agent=None)`**
  - Check if a URL is allowed by robots.txt.

- **`get_domain_name(self, url)`**
  - Extract and return the domain name from a URL.

- **`is_valid_link(self, url)`**
  - Check if a URL is valid, not blacklisted, and meets the whitelist criteria.

- **`fetch_page(self, url, max_retries=3)`**
  - Fetch the content of a page with rate limiting and retry logic.

- **`fetch_with_playwright(self, url)`**
  - Fetch the content of a page using Playwright.

- **`process_url(self, url, depth)`**
  - Process a URL: fetch content, parse links, and enqueue valid links.

- **`worker(self)`**
  - Worker task to process URLs from the queue.

- **`scrape_website(self, start_url, blacklisted_domains=None, blacklisted_paths=None, whitelisted_paths=None)`**
  - Start scraping a website from the start URL.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contact

For any questions or issues, please open an issue on GitHub or contact the maintainer directly.

---

Happy Scraping! 🚀