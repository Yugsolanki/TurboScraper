import aiohttp
import asyncio
from validators import url as is_valid_url_func
from urllib.parse import urljoin, urlparse
import logging
import playwright.async_api as playwright
from bs4 import BeautifulSoup
import time
import re
import os
from robotsparser.parser import Robotparser


class AsyncParallelWebScraper:
    def __init__(self, max_concurrent=10, timeout=10, max_retries=3, max_depth=3, rate_limit_delay=1, respect_robots=True, user_agents=['MyScraper/1.0 (contact@example.com)']):
        """
        Initialize the AsyncParallelWebScraper with specified parameters.

        :param max_concurrent: Maximum number of concurrent connections.
        :param timeout: Timeout for each request.
        :param max_retries: Maximum number of retries for failed requests.
        :param max_depth: Maximum depth for URL crawling.
        :param rate_limit_delay: Delay between requests to the same domain.
        :param respect_robots: Whether to respect robots.txt rules.
        :param user_agents: List of user agents to rotate.
        """
        self.max_concurrent = max_concurrent        # maximum number of tasks that run at the same time : 10
        # how long to wait for a response before giving up :
        self.timeout = timeout
        # maximum number of times to retry a failed request
        self.max_retries = max_retries
        # how deep the crawler goes when following links
        self.max_depth = max_depth
        # delay between requests to the same website measured in seconds
        self.rate_limit_delay = rate_limit_delay
        # whether to follow the website's robot rules
        self.respect_robots = respect_robots
        # list of identifiers to pretend to be different browsers/devices
        self.user_agents = user_agents
        # index to track the current user agent in use
        self.current_user_agent = 0
        # placeholder for a network session used for HTTP requests
        self.session = None
        # ensures that only one task accesses shared data at a time
        self.lock = asyncio.Lock()
        # collection of URLs that have already been scraped
        self.scraped_urls = set()
        # collection of URLs leading to external domains
        self.external_links = set()
        # queue of URLs waiting to be processed
        self.to_scrape = asyncio.Queue()
        # collection of URLs currently being processed
        self.processing = set()
        # set of website domains allowed for crawling
        self.whitelisted_domains = set()
        # set of domains that should not be crawled
        self.blacklisted_domains = set()
        # list of patterns for website paths not to scan
        self.blacklisted_paths_patterns = []
        # list of patterns for website paths permitted for scanning
        self.whitelisted_paths_patterns = []
        # limits concurrent use of the Playwright tool based on CPU count
        self.playwright_semaphore = asyncio.Semaphore(
            max(os.cpu_count() or 1, 1))
        # records average response times for each domain
        self.domain_response_times = {}
        # tracks the last access time for each domain to enforce rate limits
        self.rate_limits = {}
        # signals when tasks should stop processing
        self.cancel_event = asyncio.Event()
        # stores parsers for each domain's robots.txt to check permissions
        self.robots_parsers = {}

    def add_to_blacklist(self, domain=None, paths=None, path_patterns=None):
        """
        Add domains and path patterns to the blacklist.

        :param domain: Single domain or list of domains to blacklist.
        :param paths: Single regex pattern or list of patterns for paths to blacklist.
        :param path_patterns: Single regex pattern or list of patterns for paths to blacklist.
        """
        if domain:
            if isinstance(domain, str):
                self.blacklisted_domains.add(domain.lower())
            elif isinstance(domain, list):
                self.blacklisted_domains.update([d.lower() for d in domain])
        if paths:
            if isinstance(paths, str):
                self.blacklisted_paths_patterns.append(
                    re.compile(paths))
            elif isinstance(paths, list):
                self.blacklisted_paths_patterns.extend(
                    [re.compile(p) for p in paths])
        if path_patterns:
            if isinstance(path_patterns, str):
                self.blacklisted_paths_patterns.append(
                    re.compile(path_patterns))
            elif isinstance(path_patterns, list):
                self.blacklisted_paths_patterns.extend(
                    [re.compile(p) for p in path_patterns])

    def add_to_whitelist(self, domain=None, paths=None, path_patterns=None):
        """
        Add domains and path patterns to the whitelist.

        :param domain: Single domain or list of domains to whitelist.
        :param paths: Single regex pattern or list of patterns for paths to whitelist.
        :param path_patterns: Single regex pattern or list of patterns for paths to whitelist.
        """
        if domain:
            if isinstance(domain, str):
                self.whitelisted_domains.add(domain.lower())
            elif isinstance(domain, list):
                self.whitelisted_domains.update([d.lower() for d in domain])
        if paths:
            if isinstance(paths, str):
                self.whitelisted_paths_patterns.append(
                    re.compile(paths))
            elif isinstance(paths, list):
                self.whitelisted_paths_patterns.extend(
                    [re.compile(p) for p in paths])
        if path_patterns:
            if isinstance(path_patterns, str):
                self.whitelisted_paths_patterns.append(
                    re.compile(path_patterns))
            elif isinstance(path_patterns, list):
                self.whitelisted_paths_patterns.extend(
                    [re.compile(p) for p in path_patterns])

    async def initialize(self):
        """
        Initialize aiohttp session and playwright browser.
        """
        self.session = aiohttp.ClientSession()
        try:
            self.playwright = await playwright.async_playwright().start()

            self.browser = await asyncio.to_thread(
                lambda: self.playwright.chromium.launch()
            )
        except NotImplementedError:
            self.playwright = await playwright.async_playwright().start()

            self.browser = await asyncio.to_thread(
                lambda: self.playwright.chromium.launch()
            )
        except Exception as e:
            logging.error(f"Error initializing Playwright: {e}")
            raise e

    async def close(self):
        """
        Close aiohttp session and playwright browser.
        """
        await self.session.close()
        await self.browser.close()
        await self.playwright.stop()

    def get_headers(self):
        """
        Get headers with rotated User-Agent.

        :return: Dictionary of headers.
        """
        headers = {
            'User-Agent': self.user_agents[self.current_user_agent],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        self.current_user_agent = (
            self.current_user_agent + 1) % len(self.user_agents)
        return headers

    async def get_robots_parser(self, domain):
        """
        Retrieve or fetch the robots.txt parser for a given domain.

        :param domain: The domain name.
        :return: The robots parser or None if fetch fails.
        """
        if domain in self.robots_parsers:
            return self.robots_parsers[domain]
        robots_url = f"http://{domain}/robots.txt"
        try:
            rb = Robotparser(url=robots_url, verbose=True)
            await asyncio.to_thread(rb.read, fetch_sitemap_urls=True, sitemap_url_crawl_limit=5)
            self.robots_parsers[domain] = rb
            return rb
        except Exception as e:
            logging.warning(
                f"Failed to fetch or parse robots.txt for {domain}: {e}")
            return None

    async def is_allowed(self, url, user_agent=None):
        """
        Check if a URL is allowed by robots.txt.

        :param url: The URL to check.
        :param user_agent: The user agent to use for the check.
        :return: True if allowed, False otherwise.
        """
        domain = self.get_domain_name(url)
        rb = await self.get_robots_parser(domain)
        if rb:
            if user_agent is None:
                user_agent = self.user_agents[self.current_user_agent]
            return rb.is_allowed(user_agent, url)
        return True  # If no robots.txt, assume allowed

    def get_domain_name(self, url):
        """
        Extract and return the domain name from a URL.

        :param url: The URL.
        :return: The domain name.
        """
        return urlparse(url).hostname.lower() if urlparse(url).hostname else ''

    def is_valid_link(self, url):
        """
        Check if a URL is valid, not blacklisted, and meets the whitelist criteria.

        :param url: The URL to validate.
        :return: True if the URL is valid and allowed, False otherwise.
        """
        if not is_valid_url_func(url):
            return False
        domain = self.get_domain_name(url)
        parsed_url = urlparse(url)
        path = parsed_url.path.lower()

        # Check against blacklisted domains
        if domain in self.blacklisted_domains:
            return False

        # Check against blacklisted path patterns
        if any(pattern.match(path) for pattern in self.blacklisted_paths_patterns):
            return False

        # Check against whitelisted path patterns if any are set
        if self.whitelisted_paths_patterns:
            if not any(pattern.match(path) for pattern in self.whitelisted_paths_patterns):
                return False

        return True

    async def fetch_page(self, url, max_retries=3):
        """
        Fetch the content of a page with rate limiting and retry logic.

        :param url: The URL to fetch.
        :param max_retries: Maximum number of retries.
        :return: The page content or None if failed.
        """
        domain = self.get_domain_name(url)
        now = time.monotonic()
        if domain in self.rate_limits:
            delay = self.rate_limits[domain] + self.rate_limit_delay - now
            if delay > 0:
                await asyncio.sleep(delay)  # Delay to respect rate limit
        current_timeout = self.domain_response_times.get(domain, self.timeout)
        headers = self.get_headers()
        for attempt in range(max_retries + 1):
            try:
                start_time = time.monotonic()
                async with self.session.get(url, headers=headers, timeout=current_timeout) as response:
                    elapsed_time = time.monotonic() - start_time
                    if domain in self.domain_response_times:
                        self.domain_response_times[domain] = (
                            self.domain_response_times[domain] + elapsed_time) / 2
                    else:
                        self.domain_response_times[domain] = elapsed_time
                    self.timeout = self.domain_response_times[domain] + 2
                    logging.debug(
                        f"Adjusted timeout for {domain}: {self.timeout:.2f} seconds")
                    if response.status == 200:
                        content_type = response.headers.get(
                            'Content-Type', '').lower()
                        if 'text/html' in content_type:
                            content = await response.read()
                            self.rate_limits[domain] = time.monotonic()
                            return content
                        else:
                            logging.info(f"Skipping non-HTML content: {url}")
                            return None
                    else:
                        logging.warning(
                            f"Non-200 status code {response.status} for {url}")
                        return None
            except Exception as e:
                delay = 2 ** attempt
                if delay > 16:
                    delay = 16
                logging.warning(
                    f"Attempt {attempt + 1} failed to fetch {url}: {e}. Retrying in {delay} seconds.")
                await asyncio.sleep(delay)
        logging.error(f"Max retries reached for {url}")
        return None

    async def fetch_with_playwright(self, url):
        """
        Fetch the content of a page using Playwright.

        :param url: The URL to fetch.
        :return: The page content or None if failed.
        """
        domain = self.get_domain_name(url)
        now = time.monotonic()
        if domain in self.rate_limits:
            delay = self.rate_limits[domain] + self.rate_limit_delay - now
            if delay > 0:
                await asyncio.sleep(delay)
        headers = self.get_headers()
        async with self.playwright_semaphore:
            page = await self.browser.new_page(extra_http_headers=headers)
            try:
                await page.goto(url)
                content = await page.content()
                self.rate_limits[domain] = time.monotonic()
            except Exception as e:
                logging.error(f"Error fetching {url} with Playwright: {e}")
                content = None
            finally:
                await page.close()
            return content.encode('utf-8') if content else None

    async def process_url(self, url, depth):
        """
        Process a URL: fetch content, parse links, and enqueue valid links.

        :param url: The URL to process.
        :param depth: The current depth of the URL.
        """
        if self.respect_robots:
            allowed = await self.is_allowed(url)
            if not allowed:
                logging.info(f"Robots.txt disallows crawling {url}")
                return
        async with self.lock:
            if url in self.scraped_urls or url in self.processing:
                return
            if depth > self.max_depth:
                return
            domain = self.get_domain_name(url)
            if domain in self.blacklisted_domains:
                logging.info(f"Skipping blacklisted domain: {url}")
                return
            parsed_url = urlparse(url)
            path = parsed_url.path.lower()
            if any(pattern.match(path) for pattern in self.blacklisted_paths_patterns):
                logging.info(f"Skipping blacklisted path: {url}")
                return
            if self.whitelisted_paths_patterns:
                if not any(pattern.match(path) for pattern in self.whitelisted_paths_patterns):
                    logging.info(f"Skipping non-whitelisted path: {url}")
                    return
            self.processing.add(url)
        try:
            domain = self.get_domain_name(url)
            if domain in self.whitelisted_domains:
                content = await self.fetch_with_playwright(url)
            else:
                content = await self.fetch_page(url)
            if content is None:
                return
            try:
                soup = BeautifulSoup(content, 'html.parser')
            except Exception as e:
                logging.error(f"Error parsing HTML for {url}: {e}")
                return
            links = []
            for link in soup.find_all('a'):
                href = link.get('href')
                if href:
                    abs_link = urljoin(url, href)
                    links.append(abs_link)
            for link in links:
                if not self.is_valid_link(link):
                    continue
                if link.startswith('http://'):
                    https_link = link.replace('http://', 'https://')
                    try:
                        async with self.session.head(https_link, timeout=5):
                            link = https_link
                    except aiohttp.ClientError:
                        logging.info(
                            f"HTTPS not available for {link}, using HTTP.")
                domain = self.get_domain_name(link)
                if any(domain == main_domain for main_domain in self.whitelisted_domains):
                    if link not in self.scraped_urls and link not in self.processing:
                        await self.to_scrape.put((link, depth + 1))
                else:
                    async with self.lock:
                        self.external_links.add(link)
        except Exception as e:
            logging.error(f"Error processing {url}: {e}")
        finally:
            async with self.lock:
                self.scraped_urls.add(url)
                self.processing.remove(url)

    async def worker(self):
        """
        Worker task to process URLs from the queue.
        """
        current_task = asyncio.current_task()
        logging.debug(f"Worker task started: {current_task.get_name()}")
        while True:
            if self.cancel_event.is_set():
                logging.debug(
                    f"Worker task {current_task.get_name()} exiting gracefully")
                break
            try:
                url, depth = await self.to_scrape.get()
                logging.debug(
                    f"Task {current_task.get_name()} processing {url}")
                await self.process_url(url, depth)
                self.to_scrape.task_done()
            except asyncio.CancelledError:
                logging.debug(
                    f"Worker task {current_task.get_name()} cancelled")
                break
            except Exception as e:
                logging.error(
                    f"Worker task {current_task.get_name()} error: {e}")
                self.to_scrape.task_done()

    async def scrape_website(self, start_url, whitelisted_domains=None, blacklisted_domains=None, blacklisted_paths=None, whitelisted_paths=None):
        """
        Start scraping a website from the start URL.

        :param start_url: The initial URL to start crawling.
        :param blacklisted_domains: List of domains to blacklist.
        :param blacklisted_paths: List of path patterns to blacklist.
        :param whitelisted_paths: List of path patterns to whitelist.
        :return: Tuple of scraped URLs and external links.
        """
        main_domain = self.get_domain_name(start_url)

        if whitelisted_domains:
            if isinstance(whitelisted_domains, str):
                self.whitelisted_domains.add(whitelisted_domains.lower())
            elif isinstance(whitelisted_domains, list):
                self.whitelisted_domains.update(
                    [d.lower() for d in whitelisted_domains])
        else:
            self.whitelisted_domains.add(main_domain)

        if blacklisted_domains:
            self.add_to_blacklist(domain=blacklisted_domains)
        if blacklisted_paths:
            self.add_to_blacklist(path_patterns=blacklisted_paths)
        if whitelisted_paths:
            self.add_to_whitelist(path_patterns=whitelisted_paths)
        initial_depth = 0
        await self.to_scrape.put((start_url, initial_depth))
        tasks = []
        for _ in range(self.max_concurrent):
            task = asyncio.create_task(self.worker())
            tasks.append(task)
        await self.to_scrape.join()
        self.cancel_event.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.scraped_urls, self.external_links


async def main():
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        handlers=[
                            logging.StreamHandler(),
                            logging.FileHandler('scraper.log', mode='w')
                        ])

    start_url = "https://newindia.co.in/"
    whitelisted_domains = ['https://www.insuranceinstituteofindia.com/',]
    # blacklisted_domains = ['blacklisteddomain.com']
    # blacklisted_paths = [r'/blacklisted/path.*', r'/another/blacklisted/path.*']
    # [r'/whitelisted/path.*', r'/another/whitelisted/path.*']
    # whitelisted_paths = [r'/docs/api/checkout-ui-extensions.*']
    scraper = AsyncParallelWebScraper(max_concurrent=14, max_depth=float(
        'inf'), rate_limit_delay=1, respect_robots=False)

    await scraper.initialize()
    # scraper.add_to_blacklist(domain=blacklisted_domains, path_patterns=blacklisted_paths)
    scraper.add_to_whitelist(
        domain=whitelisted_domains)
    start_time = time.time()
    scraped_links, external_links = await scraper.scrape_website(start_url)
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
