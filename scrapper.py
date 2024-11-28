import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import logging
import threading

class ParallelWebScraper:
    def __init__(self, max_workers=10, timeout=10, max_retries=3):
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.scraped_urls = set()
        self.external_links = set()
        self.to_scrape = deque()
        self.processing = set()
        self.white_list_domains = set()

    def is_valid_url(self, url):
        """Check if url is valid."""
        try:
            parsed = urlparse(url)
            return bool(parsed.netloc) and bool(parsed.scheme)
        except ValueError:
            return False

    def get_domain_name(self, url):
        """Extract domain name from the given URL."""
        return urlparse(url).netloc

    def get_all_links(self, url):
        """Extract all links from the given URL with retry mechanism."""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                response.encoding = 'utf-8'
                soup = BeautifulSoup(response.text, 'html.parser')
                return [urljoin(url, link.get('href')) 
                        for link in soup.find_all('a') 
                        if link.get('href')]
            except (requests.exceptions.HTTPError, 
                    requests.exceptions.ConnectionError, 
                    requests.exceptions.Timeout) as e:
                logging.warning(f"Attempt {attempt + 1} failed to fetch {url}: {e}")
                if attempt == self.max_retries:
                    logging.error(f"Max retries reached for {url}")
                    return []
            except Exception as e:
                logging.error(f"Error fetching {url}: {e}")
                return []

    def process_url(self, url):
        """Process a single URL and return its links."""
        if not url or not self.is_valid_url(url):
            return set(), set()

        if url in self.scraped_urls:
            return set(), set()

        links = self.get_all_links(url)
        internal_links = set()
        external_links = set()

        for link in links:
            if not self.is_valid_url(link):
                logging.warning(f"Invalid link found: {link}")
                continue
            domain = self.get_domain_name(link)
            if domain in self.white_list_domains:
                internal_links.add(link)
            else:
                external_links.add(link)

        return internal_links, external_links

    def scrape_website(self, start_url):
        """Scrape website starting from start_url using parallel processing."""
        self.white_list_domains = set([self.get_domain_name(start_url), "www.shahandanchor.com"])
        with self.lock:
            if start_url not in self.to_scrape:
                self.to_scrape.append(start_url)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {}
            while self.to_scrape or self.processing:
                # Submit new tasks
                while self.to_scrape and len(self.processing) < self.max_workers:
                    url = self.to_scrape.popleft()
                    with self.lock:
                        if url in self.scraped_urls or url in self.processing:
                            continue
                        self.processing.add(url)
                    future = executor.submit(self.process_batch, url)
                    future_to_url[future] = url

                # Process completed tasks
                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        future.result()
                    except Exception as e:
                        logging.error(f"Error processing {url}: {e}")
                        with self.lock:
                            if url in self.processing:
                                self.processing.remove(url)

                # Small delay to prevent CPU overuse
                time.sleep(0.1)

        return self.scraped_urls, self.external_links

    def process_batch(self, url):
        """Process a batch of URLs and update the shared data structures."""
        internal_links, external_links = self.process_url(url)
        with self.lock:
            if url in self.processing:
                self.scraped_urls.add(url)
                self.external_links.update(external_links)
                # Add new internal links to processing queue
                for link in internal_links:
                    if link not in self.scraped_urls and link not in self.processing:
                        self.to_scrape.append(link)
                self.processing.remove(url)
            else:
                logging.warning(f"URL {url} already processed or scraped.")

def write_links_to_file(filename, links):
    """Write links to file with proper encoding."""
    try:
        with open(filename, "w", encoding='utf-8') as file:
            for link in links:
                file.write(f"{link}\n")
    except Exception as e:
        logging.error(f"Error writing to {filename}: {e}")

def main():
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    start_url = "https://www.sakec.ac.in/"
    scraper = ParallelWebScraper(max_workers=10)
    
    start_time = time.time()
    scraped_links, external_links = scraper.scrape_website(start_url)
    end_time = time.time()
    
    print(f"\nScraped Links: {len(scraped_links)}")
    print(f"External Links: {len(external_links)}")
    print(f"Time taken: {end_time - start_time:.2f} seconds")
    
    # Write to files with proper encoding
    write_links_to_file("scraped_links.txt", scraped_links)
    write_links_to_file("external_links.txt", external_links)

if __name__ == "__main__":
    main()