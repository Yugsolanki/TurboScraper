import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import logging
import random
import threading

class ParallelWebScraper:
    def __init__(self, max_workers=10, timeout=10, max_retries=3, max_depth=3):
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_depth = max_depth
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
        self.lock = threading.Lock()
        self.scraped_urls = set()
        self.external_links = set()
        self.to_scrape = deque()
        self.processing = set()
        self.white_list_domains = set()
        self.delay_min = 0.5
        self.delay_max = 1.0

    def is_subdomain(self, candidate, main_domain):
        candidate_parts = candidate.split('.')
        main_parts = main_domain.split('.')
        if len(candidate_parts) < len(main_parts):
            return False
        return candidate_parts[-len(main_parts):] == main_parts

    def is_valid_url(self, url):
        try:
            parsed = urlparse(url)
            return bool(parsed.netloc) and bool(parsed.scheme)
        except ValueError:
            return False

    def get_domain_name(self, url):
        return urlparse(url).netloc.lower()

    def get_all_links(self, url, response):
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            logging.info(f"Skipping non-HTML content: {url}")
            return []
        soup = BeautifulSoup(response.content, 'html.parser')
        links = [urljoin(url, link.get('href')) 
                 for link in soup.find_all('a') 
                 if link.get('href')]
        time.sleep(random.uniform(self.delay_min, self.delay_max))
        return links

    def process_url(self, url, depth):
        if not self.is_valid_url(url) or url in self.scraped_urls:
            return set(), set()

        internal_links = set()
        external_links = set()

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                links = self.get_all_links(url, response)
                break
            except (requests.exceptions.HTTPError, 
                    requests.exceptions.ConnectionError, 
                    requests.exceptions.Timeout) as e:
                logging.warning(f"Attempt {attempt + 1} failed to fetch {url}: {e}")
                if attempt == self.max_retries:
                    logging.error(f"Max retries reached for {url}")
                    return internal_links, external_links
            except Exception as e:
                logging.error(f"Error fetching {url}: {e}")
                return internal_links, external_links

        for link in links:
            if not self.is_valid_url(link):
                logging.warning(f"Invalid link found: {link}")
                continue
            domain = self.get_domain_name(link)
            if any(self.is_subdomain(domain, main_domain) for main_domain in self.white_list_domains):
                internal_links.add((link, depth + 1))
            else:
                external_links.add(link)

        return internal_links, external_links

    def scrape_website(self, start_url):
        main_domain = self.get_domain_name(start_url)
        self.white_list_domains = {main_domain}
        initial_depth = 0
        self.to_scrape.append((start_url, initial_depth))

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {}
            while self.to_scrape or self.processing:
                while self.to_scrape and len(self.processing) < self.max_workers:
                    url, depth = self.to_scrape.popleft()
                    with self.lock:
                        if url in self.scraped_urls or url in self.processing:
                            continue
                        if depth > self.max_depth:
                            continue
                        self.processing.add(url)
                    future = executor.submit(self.process_batch, url, depth)
                    future_to_url[future] = url

                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        future.result()
                    except Exception as e:
                        logging.error(f"Error processing {url}: {e}")
                        with self.lock:
                            if url in self.processing:
                                self.processing.remove(url)

                time.sleep(0.1)

        return self.scraped_urls, self.external_links

    def process_batch(self, url, depth):
        internal_links, external_links = self.process_url(url, depth)
        with self.lock:
            if url in self.processing:
                self.scraped_urls.add(url)
                self.external_links.update(external_links)
                for link, link_depth in internal_links:
                    if link not in self.scraped_urls and link not in self.processing and link_depth <= self.max_depth:
                        self.to_scrape.append((link, link_depth))
                self.processing.remove(url)
            else:
                logging.warning(f"URL {url} already processed or scraped.")

def write_links_to_file(filename, links):
    try:
        with open(filename, "w", encoding='utf-8') as file:
            for link in links:
                file.write(f"{link}\n")
    except Exception as e:
        logging.error(f"Error writing to {filename}: {e}")

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    start_url = "https://crawler-test.com/"
    scraper = ParallelWebScraper(max_workers=10, max_depth=3)
    start_time = time.time()
    scraped_links, external_links = scraper.scrape_website(start_url)
    end_time = time.time()
    print(f"\nScraped Links: {len(scraped_links)}")
    print(f"External Links: {len(external_links)}")
    print(f"Time taken: {end_time - start_time:.2f} seconds")
    write_links_to_file("scraped_links.txt", scraped_links)
    write_links_to_file("external_links.txt", external_links)

if __name__ == "__main__":
    main()