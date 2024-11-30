import aiohttp
import asyncio
from validators import url as is_valid_url_func
from urllib.parse import urljoin, urlparse, urlsplit
import logging
import playwright.async_api as playwright
from bs4 import BeautifulSoup
import time
import tldextract
import re
import os

class AsyncParallelWebScraper:
    def __init__(self, max_concurrent=10, timeout=10, max_retries=3, max_depth=3, rate_limit_delay=1):
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_depth = max_depth
        self.rate_limit_delay = rate_limit_delay
        self.session = None
        self.lock = asyncio.Lock()
        self.scraped_urls = set()
        self.external_links = set()
        self.to_scrape = asyncio.Queue()
        self.processing = set()
        self.white_list_domains = set()
        self.blacklisted_domains = set()
        self.blacklisted_paths_patterns = []
        self.whitelisted_paths_patterns = []
        self.playwright_semaphore = asyncio.Semaphore(max(os.cpu_count() or 1, 1))  # Adjust semaphore limit
        self.domain_response_times = {}
        self.cancel_event = asyncio.Event()

    def add_to_blacklist(self, domain=None, paths=None, path_patterns=None):
        if domain:
            if isinstance(domain, str):
                self.blacklisted_domains.add(domain.lower())
            elif isinstance(domain, list):
                self.blacklisted_domains.update([d.lower() for d in domain])
        if path_patterns:
            if isinstance(path_patterns, str):
                self.blacklisted_paths_patterns.append(re.compile(path_patterns))
            elif isinstance(path_patterns, list):
                self.blacklisted_paths_patterns.extend([re.compile(p) for p in path_patterns])

    def add_to_whitelist(self, path_patterns=None):
        if path_patterns:
            if isinstance(path_patterns, str):
                self.whitelisted_paths_patterns.append(re.compile(path_patterns))
            elif isinstance(path_patterns, list):
                self.whitelisted_paths_patterns.extend([re.compile(p) for p in path_patterns])

    async def initialize(self):
        self.session = aiohttp.ClientSession()
        self.playwright = await playwright.async_playwright().start()
        self.browser = await self.playwright.firefox.launch()

    async def close(self):
        await self.session.close()
        await self.browser.close()
        await self.playwright.stop()

    def is_subdomain(self, candidate, main_domain):
        candidate_extract = tldextract.extract(candidate)
        main_extract = tldextract.extract(main_domain)
        candidate_domain = candidate_extract.subdomain + '.' + candidate_extract.domain + '.' + candidate_extract.suffix
        main_domain_full = main_extract.domain + '.' + main_extract.suffix
        if candidate_extract.subdomain == '':
            return candidate_extract.domain == main_extract.domain and candidate_extract.suffix == main_extract.suffix
        return candidate_domain.endswith(main_domain_full)

    def get_domain_name(self, url):
        return urlparse(url).hostname.lower() if urlparse(url).hostname else ''

    async def fetch_page(self, url, max_retries=3):
        domain = self.get_domain_name(url)
        current_timeout = self.domain_response_times.get(domain, self.timeout)
        for attempt in range(max_retries + 1):
            try:
                start_time = time.monotonic()
                async with self.session.get(url, timeout=current_timeout) as response:
                    elapsed_time = time.monotonic() - start_time
                    if domain in self.domain_response_times:
                        self.domain_response_times[domain] = (self.domain_response_times[domain] + elapsed_time) / 2
                    else:
                        self.domain_response_times[domain] = elapsed_time
                    self.timeout = self.domain_response_times[domain] + 2
                    logging.debug(f"Adjusted timeout for {domain}: {self.timeout:.2f} seconds")
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '').lower()
                        if 'text/html' in content_type:
                            content = await response.read()
                            return content
                        else:
                            logging.info(f"Skipping non-HTML content: {url}")
                            return None
                    else:
                        logging.warning(f"Non-200 status code {response.status} for {url}")
                        return None
            except Exception as e:
                delay = 2 ** attempt
                if delay > 16:
                    delay = 16
                logging.warning(f"Attempt {attempt + 1} failed to fetch {url}: {e}. Retrying in {delay} seconds.")
                await asyncio.sleep(delay)
        logging.error(f"Max retries reached for {url}")
        return None

    async def fetch_with_playwright(self, url):
        async with self.playwright_semaphore:
            page = await self.browser.new_page()
            try:
                await page.goto(url)
                content = await page.content()
            except Exception as e:
                logging.error(f"Error fetching {url} with Playwright: {e}")
                content = None
            finally:
                await page.close()
            return content.encode('utf-8') if content else None

    async def process_url(self, url, depth):
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
            if 'crawler-test.com' in url:
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
                if not is_valid_url_func(link):
                    logging.warning(f"Invalid link found: {link}")
                    continue
                if link.startswith('http://'):
                    https_link = link.replace('http://', 'https://')
                    try:
                        async with self.session.head(https_link, timeout=5):
                            link = https_link
                    except aiohttp.ClientError:
                        logging.info(f"HTTPS not available for {link}, using HTTP.")
                domain = self.get_domain_name(link)
                if any(self.is_subdomain(domain, main_domain) for main_domain in self.white_list_domains):
                    parsed_link = urlparse(link)
                    path = parsed_link.path.lower()
                    if domain in self.blacklisted_domains or any(pattern.match(path) for pattern in self.blacklisted_paths_patterns):
                        logging.info(f"Skipping blacklisted link: {link}")
                        continue
                    if self.whitelisted_paths_patterns and not any(pattern.match(path) for pattern in self.whitelisted_paths_patterns):
                        logging.info(f"Skipping non-whitelisted link: {link}")
                        continue
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
        current_task = asyncio.current_task()
        logging.debug(f"Worker task started: {current_task.get_name()}")
        while True:
            if self.cancel_event.is_set():
                logging.debug(f"Worker task {current_task.get_name()} exiting gracefully")
                break
            try:
                url, depth = await self.to_scrape.get()
                logging.debug(f"Task {current_task.get_name()} processing {url}")
                await self.process_url(url, depth)
                self.to_scrape.task_done()
            except asyncio.CancelledError:
                logging.debug(f"Worker task {current_task.get_name()} cancelled")
                break
            except Exception as e:
                logging.error(f"Worker task {current_task.get_name()} error: {e}")
                self.to_scrape.task_done()

    async def scrape_website(self, start_url, blacklisted_domains=None, blacklisted_paths=None, whitelisted_paths=None):
        main_domain = self.get_domain_name(start_url)
        self.white_list_domains = {main_domain}
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
    start_url = "https://crawler-test.com/"
    blacklisted_domains = ['blacklisteddomain.com']
    blacklisted_paths = [r'/blacklisted/path.*', r'/another/blacklisted/path.*']
    whitelisted_paths = []  # [r'/whitelisted/path.*', r'/another/whitelisted/path.*']
    scraper = AsyncParallelWebScraper(max_concurrent=10, max_depth=3, rate_limit_delay=1)
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