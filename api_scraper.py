from contextlib import asynccontextmanager
import uuid
from fastapi.responses import JSONResponse
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, BackgroundTasks
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
from typing import Dict, List, Set, Tuple, Optional, Union, Any
from pydantic import BaseModel, Field, HttpUrl, field_validator
import traceback


class ScraperConfig(BaseModel):
    max_concurrent: int = Field(default=10)
    timeout: int = Field(default=10)
    playwright_timeout: int = Field(default=30)
    max_retries: int = Field(default=3)
    max_depth: float = Field(default=float('inf'))
    rate_limit_delay: float = Field(default=1.0)
    respect_robots: bool = Field(default=False)
    user_agents: Optional[List[str]] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"]
    )


class ScrapeRequest(BaseModel):
    start_url: str
    whitelisted_domains: Optional[List[str]] = None
    blacklisted_domains: Optional[List[str]] = None
    blacklisted_paths: Optional[List[str]] = None
    whitelisted_paths: Optional[List[str]] = None


class ScapeJob(BaseModel):
    """Configuration for the scraping job API."""
    max_concurrent: int = Field(default=10)
    timeout: int = Field(default=10)
    max_retries: int = Field(default=3)
    max_depth: float = Field(default=float('inf'))
    rate_limit_delay: float = Field(default=1.0)
    respect_robots: bool = Field(default=False)
    user_agents: Optional[List[str]] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"]
    )
    start_url: str
    whitelisted_domains: Optional[List[str]] = None
    blacklisted_domains: Optional[List[str]] = None
    blacklisted_paths: Optional[List[str]] = None
    whitelisted_paths: Optional[List[str]] = None


class ScrapeResponse(BaseModel):
    """Response model for the scraping job API."""
    scraped_urls: List[str] = []
    external_links: List[str] = []
    job_id: str = None
    status: str = "completed"
    error: Optional[str] = None


class AsyncParallelWebScraper:
    def __init__(self, config: ScraperConfig = None):
        """
        Initialize the AsyncParallelWebScraper with specified parameters.

        :param config: Configuration object for the scraper
        """
        if config is None:
            config = ScraperConfig()

        self.max_concurrent = config.max_concurrent
        self.timeout = config.timeout
        self.playwright_timeout = config.playwright_timeout
        self.max_retries = config.max_retries
        self.max_depth = config.max_depth
        self.rate_limit_delay = config.rate_limit_delay
        self.respect_robots = config.respect_robots
        self.user_agents = config.user_agents if config.user_agents else [
            "MyScraper/1.0 (contact@example.com)"]

        # State variables
        self.current_user_agent = 0
        self.session: Optional[aiohttp.ClientSession] = None
        self.lock = asyncio.Lock()
        self.scraped_urls: Set[str] = set()
        self.external_links: Set[str] = set()
        self.to_scrape: Optional[asyncio.Queue] = None
        self.processing: Set[str] = set()
        self.whitelisted_domains: Set[str] = set()
        self.blacklisted_domains: Set[str] = set()
        self.blacklisted_paths_patterns: List[re.Pattern] = []
        self.whitelisted_paths_patterns: List[re.Pattern] = []
        self.domain_response_times: Dict[str, float] = {}
        self.rate_limits: Dict[str, float] = {}
        self.cancel_event: Optional[asyncio.Event] = None
        self.robots_parsers: Dict[str, Robotparser] = {}

        # Playwright resources
        self.playwright = None
        self.browser = None

        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - %(lineno)d - %(funcName)s',
            datefmt='%d-%m-%Y %H:%M:%S'
        )
        self.logger = logging.getLogger("scraper")

    async def initialize(self) -> None:
        """Initialize aiohttp session and playwright browser."""
        if self.session is not None:
            return  # Already initialized

        self.session = aiohttp.ClientSession()
        self.to_scrape = asyncio.Queue()
        self.cancel_event = asyncio.Event()
        self.playwright_semaphore = asyncio.Semaphore(
            max(os.cpu_count() or 1, 1))

        try:
            self.playwright = await playwright.async_playwright().start()

            try:
                self.browser = await self.playwright.chromium.launch(headless=True)
            except NotImplementedError:
                self.logger.error(
                    "Chromium is not installed. Please install it. Launching firefox instead.")
                self.browser = await self.playwright.firefox.launch(headless=True)
            self.logger.info("Playwright browser initialized successfully")
        except Exception as e:
            self.logger.error(f"Error initializing Playwright: {e}")
            # Clean up session if browser initialization fails
            await self.session.close()
            self.session = None
            raise RuntimeError(f"Failed to initialize browser: {str(e)}")

    async def close(self) -> None:
        """Close all resources."""
        if self.session:
            await self.session.close()
            self.session = None

        if self.browser:
            await self.browser.close()
            self.browser = None

        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    def add_to_blacklist(self, domain: Union[str, List[str]] = None,
                         paths: Union[str, List[str]] = None) -> None:
        """
        Add domains and path patterns to the blacklist.

        :param domain: Single domain or list of domains to blacklist
        :param paths: Single regex pattern or list of patterns for paths to blacklist
        """
        if domain:
            if isinstance(domain, str):
                self.blacklisted_domains.add(domain.lower())
            elif isinstance(domain, list):
                self.blacklisted_domains.update([d.lower() for d in domain])

        if paths:
            if isinstance(paths, str):
                self.blacklisted_paths_patterns.append(re.compile(paths))
            elif isinstance(paths, list):
                self.blacklisted_paths_patterns.extend(
                    [re.compile(p) for p in paths])

    def add_to_whitelist(self, domain: Union[str, List[str]] = None,
                         paths: Union[str, List[str]] = None) -> None:
        """
        Add domains and path patterns to the whitelist.

        :param domain: Single domain or list of domains to whitelist
        :param paths: Single regex pattern or list of patterns for paths to whitelist
        """
        if domain:
            if isinstance(domain, str):
                self.whitelisted_domains.add(domain.lower())
            elif isinstance(domain, list):
                self.whitelisted_domains.update([d.lower() for d in domain])

        if paths:
            if isinstance(paths, str):
                self.whitelisted_paths_patterns.append(re.compile(paths))
            elif isinstance(paths, list):
                self.whitelisted_paths_patterns.extend(
                    [re.compile(p) for p in paths])

    def get_headers(self) -> Dict[str, str]:
        """
        Get headers with rotated User-Agent.

        :return: Dictionary of headers
        """
        # headers = {
        #     'User-Agent': self.user_agents[self.current_user_agent],
        #     'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        #     'Accept-Language': 'en-US,en;q=0.5',
        # }
        # self.current_user_agent = (
        #     self.current_user_agent + 1) % len(self.user_agents)
        # return headers

        headers = {
            'User-Agent': self.user_agents[self.current_user_agent % len(self.user_agents)],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        self.current_user_agent = (
            self.current_user_agent + 1) % max(len(self.user_agents), 1)
        return headers

    @staticmethod
    def get_domain_name(url: str) -> str:
        """
        Extract and return the domain name from a URL.

        :param url: The URL
        :return: The domain name
        """
        return urlparse(url).hostname.lower() if urlparse(url).hostname else ''

    async def get_robots_parser(self, domain: str) -> Optional[Robotparser]:
        """
        Retrieve or fetch the robots.txt parser for a given domain.

        :param domain: The domain name
        :return: The robots parser or None if fetch fails
        """
        if domain in self.robots_parsers:
            return self.robots_parsers[domain]

        robots_url = f"http://{domain}/robots.txt"
        try:
            rb = Robotparser(url=robots_url, verbose=False)
            await asyncio.to_thread(rb.read, fetch_sitemap_urls=True, sitemap_url_crawl_limit=5)
            self.robots_parsers[domain] = rb
            return rb
        except Exception as e:
            self.logger.warning(
                f"Failed to fetch/parse robots.txt for {domain}: {e}")
            return None

    async def is_allowed(self, url: str, user_agent: str = None) -> bool:
        """
        Check if a URL is allowed by robots.txt.

        :param url: The URL to check
        :param user_agent: The user agent to use for the check
        :return: True if allowed, False otherwise
        """
        if not self.respect_robots:
            return True

        domain = self.get_domain_name(url)
        rb = await self.get_robots_parser(domain)

        if rb:
            if user_agent is None:
                user_agent = self.user_agents[self.current_user_agent]
            return rb.is_allowed(user_agent, url)
        return True  # If no robots.txt, assume allowed

    def is_valid_link(self, url: str) -> bool:
        """
        Check if a URL is valid, not blacklisted, and meets the whitelist criteria.

        :param url: The URL to validate
        :return: True if the URL is valid and allowed, False otherwise
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
        if self.whitelisted_paths_patterns and not any(pattern.match(path) for pattern in self.whitelisted_paths_patterns):
            return False

        return True

    async def fetch_page(self, url: str, max_retries: int = None) -> Optional[bytes]:
        """
        Fetch the content of a page with rate limiting and retry logic.

        :param url: The URL to fetch
        :param max_retries: Maximum number of retries
        :return: The page content or None if failed
        """
        if max_retries is None:
            max_retries = self.max_retries

        domain = self.get_domain_name(url)
        now = time.monotonic()

        # Respect rate limiting
        if domain in self.rate_limits:
            delay = self.rate_limits[domain] + self.rate_limit_delay - now
            if delay > 0:
                await asyncio.sleep(delay)

        current_timeout = self.domain_response_times.get(domain, self.timeout)
        current_timeout = min(current_timeout + 2, 30)
        headers = self.get_headers()

        for attempt in range(max_retries + 1):
            try:
                start_time = time.monotonic()
                async with self.session.get(url, headers=headers, timeout=current_timeout) as response:
                    elapsed_time = time.monotonic() - start_time

                    # Update average response time for domain
                    if domain in self.domain_response_times:
                        self.domain_response_times[domain] = (
                            self.domain_response_times[domain] + elapsed_time) / 2
                    else:
                        self.domain_response_times[domain] = elapsed_time

                    if response.status == 200:
                        content_type = response.headers.get(
                            'Content-Type', '').lower()
                        if 'text/html' in content_type:
                            content = await response.read()
                            self.rate_limits[domain] = time.monotonic()
                            return content
                        else:
                            self.logger.info(
                                f"Skipping non-HTML content: {url}")
                            return None
                    else:
                        self.logger.warning(
                            f"HTTP status {response.status} for {url}")
                        return None
            except Exception as e:
                # Exponential backoff capped at 16 seconds
                delay = min(2 ** attempt, 16)
                self.logger.warning(
                    f"Attempt {attempt + 1}/{max_retries + 1} failed for {url}: {e}. Retrying in {delay}s."
                )
                await asyncio.sleep(delay)

        self.logger.error(f"Max retries reached for {url}")
        return None

    # async def fetch_with_playwright(self, url: str) -> Optional[bytes]:
    #     """
    #     Fetch the content of a page using Playwright.

    #     :param url: The URL to fetch
    #     :return: The page content or None if failed
    #     """
    #     if not self.browser:
    #         self.logger.error("Browser not initialized")
    #         return None

    #     domain = self.get_domain_name(url)
    #     now = time.monotonic()

    #     # Respect rate limiting
    #     if domain in self.rate_limits:
    #         delay = self.rate_limits[domain] + self.rate_limit_delay - now
    #         if delay > 0:
    #             await asyncio.sleep(delay)

    #     headers = self.get_headers()

    #     async with self.playwright_semaphore:
    #         page = await self.browser.new_page(extra_http_headers=headers)
    #         try:
    #             self.logger.debug(f"Fetching {url} with Playwright")
    #             await page.goto(url, timeout=self.timeout * 1000)
    #             content = await page.content()
    #             self.rate_limits[domain] = time.monotonic()
    #             return content.encode('utf-8') if content else None
    #         except Exception as e:
    #             self.logger.error(f"Error fetching {url} with Playwright: {e}")
    #             return None
    #         finally:
    #             await page.close()

    async def fetch_with_playwright(self, url: str, max_retries: int = None) -> Optional[bytes]:
        """
        Fetch the content of a page using Playwright with retry logic.

        :param url: The URL to fetch
        :param max_retries: Maximum number of retries
        :return: The page content or None if failed
        """
        if max_retries is None:
            max_retries = self.max_retries
        
        if not self.browser:
            self.logger.error("Browser not initialized")
            return None

        domain = self.get_domain_name(url)
        now = time.monotonic()

        # Respect rate limiting
        if domain in self.rate_limits:
            delay = self.rate_limits[domain] + self.rate_limit_delay - now
            if delay > 0:
                await asyncio.sleep(delay)

        headers = self.get_headers()
        
        # Use a longer timeout for Playwright (JS-heavy pages need more time)
        # Minimum 30 seconds, or configured timeout
        playwright_timeout = max(self.timeout * 1000, 30000)

        for attempt in range(max_retries + 1):
            page = None
            try:
                async with self.playwright_semaphore:
                    page = await self.browser.new_page(extra_http_headers=headers)
                    self.logger.debug(
                        f"Fetching {url} with Playwright (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    
                    # Use "domcontentloaded" instead of "load" for faster response
                    # "load" waits for ALL resources including images, fonts, etc.
                    await page.goto(
                        url, 
                        timeout=playwright_timeout, 
                        wait_until="domcontentloaded"
                    )
                    
                    # Optional: Wait for network to be mostly idle
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        # networkidle is best-effort, continue if it times out
                        self.logger.debug(f"Network idle timeout for {url}, continuing...")
                    
                    content = await page.content()
                    self.rate_limits[domain] = time.monotonic()
                    return content.encode('utf-8') if content else None
                    
            except Exception as e:
                delay = min(2 ** attempt, 16)
                self.logger.warning(
                    f"Attempt {attempt + 1}/{max_retries + 1} failed for {url} "
                    f"with Playwright: {e}. Retrying in {delay}s."
                )
                if attempt < max_retries:
                    await asyncio.sleep(delay)
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

        self.logger.error(f"Max retries reached for {url} with Playwright")
        return None

    async def process_url(self, url: str, depth: int) -> None:
        """
        Process a URL: fetch content, parse links, and enqueue valid links.

        :param url: The URL to process
        :param depth: The current depth of the URL
        """
        # Check if we can process this URL
        async with self.lock:
            if url in self.scraped_urls or url in self.processing:
                return

            if depth > self.max_depth:
                return

            domain = self.get_domain_name(url)

            if domain in self.blacklisted_domains:
                self.logger.debug(f"Skipping blacklisted domain: {url}")
                return

            parsed_url = urlparse(url)
            path = parsed_url.path.lower()

            if any(pattern.match(path) for pattern in self.blacklisted_paths_patterns):
                self.logger.debug(f"Skipping blacklisted path: {url}")
                return

            if self.whitelisted_paths_patterns:
                if not any(pattern.match(path) for pattern in self.whitelisted_paths_patterns):
                    self.logger.debug(f"Skipping non-whitelisted path: {url}")
                    return

            self.processing.add(url)

        # Check robots.txt
        if self.respect_robots:
            allowed = await self.is_allowed(url)
            if not allowed:
                self.logger.info(f"Robots.txt disallows crawling {url}")
                async with self.lock:
                    self.processing.remove(url)
                return

        try:
            # Fetch content
            domain = self.get_domain_name(url)
            if domain in self.whitelisted_domains:
                content = await self.fetch_with_playwright(url)
            else:
                content = await self.fetch_page(url)

            if content is None:
                async with self.lock:
                    self.processing.remove(url)
                return

            # Parse HTML
            try:
                soup = BeautifulSoup(content, 'html.parser')
            except Exception as e:
                self.logger.error(f"Error parsing HTML for {url}: {e}")
                async with self.lock:
                    self.processing.remove(url)
                return

            # Extract links
            links = []
            for link in soup.find_all('a'):
                href = link.get('href')
                if href:
                    abs_link = urljoin(url, href)
                    links.append(abs_link)

            # Process extracted links
            for link in links:
                if not self.is_valid_link(link):
                    continue

                # Try HTTPS version if HTTP
                if link.startswith('http://'):
                    https_link = link.replace('http://', 'https://')
                    try:
                        async with self.session.head(https_link, timeout=5):
                            link = https_link
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        pass

                domain = self.get_domain_name(link)

                # Enqueue for crawling if in whitelisted domains
                if any(domain == main_domain for main_domain in self.whitelisted_domains):
                    async with self.lock:
                        if link not in self.scraped_urls and link not in self.processing:
                            await self.to_scrape.put((link, depth + 1))
                else:
                    async with self.lock:
                        self.external_links.add(link)
        except Exception as e:
            self.logger.error(f"Error processing {url}: {e}")
            self.logger.debug(traceback.format_exc())
        finally:
            async with self.lock:
                self.scraped_urls.add(url)
                if url in self.processing:  # Check to prevent KeyError
                    self.processing.remove(url)

    async def worker(self) -> None:
        """Worker task to process URLs from the queue."""
        task_name = asyncio.current_task().get_name()
        self.logger.debug(f"Worker {task_name} started")

        while not self.cancel_event.is_set():
            try:
                # Get URL from queue with timeout to check cancel_event periodically
                try:
                    url, depth = await asyncio.wait_for(self.to_scrape.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                self.logger.debug(f"Worker {task_name} processing {url}")
                await self.process_url(url, depth)
                self.to_scrape.task_done()
            except asyncio.CancelledError:
                self.logger.debug(f"Worker {task_name} cancelled")
                break
            except Exception as e:
                self.logger.error(f"Worker {task_name} error: {e}")
                self.logger.debug(traceback.format_exc())

                # Mark task as done even if it failed
                try:
                    self.to_scrape.task_done()
                except ValueError:
                    # Queue might be empty if exception happened after task_done()
                    pass

        self.logger.debug(f"Worker {task_name} exited")

    async def scrape_website(self, request: ScrapeRequest) -> Tuple[Set[str], Set[str]]:
        """
        Start scraping a website from the start URL.

        :param request: The scrape request containing configuration
        :return: Tuple of scraped URLs and external links
        """
        # Reset state for new scrape
        self.scraped_urls.clear()
        self.external_links.clear()
        self.processing.clear()
        self.cancel_event.clear()

        # Clear queues
        while not self.to_scrape.empty():
            try:
                self.to_scrape.get_nowait()
                self.to_scrape.task_done()
            except asyncio.QueueEmpty:
                break

        # Reset and populate blacklist/whitelist
        self.whitelisted_domains.clear()
        self.blacklisted_domains.clear()
        self.blacklisted_paths_patterns.clear()
        self.whitelisted_paths_patterns.clear()

        # Set up domains and paths for filtering
        main_domain = self.get_domain_name(request.start_url)

        if request.whitelisted_domains:
            self.whitelisted_domains = set(d.lower()
                                           for d in request.whitelisted_domains)
        else:
            self.whitelisted_domains = {main_domain}

        if request.blacklisted_domains:
            self.add_to_blacklist(domain=request.blacklisted_domains)

        if request.blacklisted_paths:
            self.add_to_blacklist(paths=request.blacklisted_paths)

        if request.whitelisted_paths:
            self.add_to_whitelist(paths=request.whitelisted_paths)

        # Start scraping
        initial_depth = 0
        await self.to_scrape.put((request.start_url, initial_depth))

        # Start worker tasks
        tasks = []
        for i in range(self.max_concurrent):
            task = asyncio.create_task(self.worker(), name=f"worker-{i}")
            tasks.append(task)

        # Wait for queue to be empty
        await self.to_scrape.join()

        # Signal workers to exit and wait for them
        self.cancel_event.set()
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        return self.scraped_urls, self.external_links
