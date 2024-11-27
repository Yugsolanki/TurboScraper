import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import threading
from collections import deque
import logging

class ParallelWebScraper:
    def __init__(self, max_workers=10, timeout=10):
        self.max_workers = max_workers
        self.timeout = timeout
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.scraped_urls = set()
        self.external_links = set()
        self.to_scrape = deque()
        self.processing = set()

    def is_valid_url(self, url):
        """Check if url is valid."""
        parsed = urlparse(url)
        return bool(parsed.netloc) and bool(parsed.scheme)

    def get_domain_name(self, url):
        """Extract domain name from the given URL."""
        return urlparse(url).netloc

    def get_all_links(self, url):
        """Extract all links from the given URL."""
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.encoding = 'utf-8'  # Ensure proper encoding of response
            soup = BeautifulSoup(response.text, 'html.parser')
            return [urljoin(url, link.get('href')) 
                   for link in soup.find_all('a') 
                   if link.get('href')]
        except Exception as e:
            logging.error(f"Error fetching {url}: {e}")
            return []

    def process_url(self, url):
        """Process a single URL and return its links."""
        if not url or url in self.scraped_urls:
            return set(), set()

        links = self.get_all_links(url)
        internal_links = set()
        external_links = set()

        for link in links:
            if not self.is_valid_url(link):
                continue
            if self.get_domain_name(link) in self.white_list_domains:
                internal_links.add(link)
            else:
                external_links.add(link)

        return internal_links, external_links

    def scrape_website(self, start_url):
        """Scrape website starting from start_url using parallel processing."""
        self.white_list_domains = set([self.get_domain_name(start_url), "www.shahandanchor.com"])
        self.to_scrape.append(start_url)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while self.to_scrape or self.processing:
                # Submit new tasks
                while self.to_scrape and len(self.processing) < self.max_workers:
                    url = self.to_scrape.popleft()
                    if url not in self.scraped_urls and url not in self.processing:
                        self.processing.add(url)
                        executor.submit(self.process_batch, url)

                # Small delay to prevent CPU overuse
                time.sleep(0.1)

        return self.scraped_urls, self.external_links

    def process_batch(self, url):
        """Process a batch of URLs and update the shared data structures."""
        try:
            internal_links, external_links = self.process_url(url)
            
            with self.lock:
                self.scraped_urls.add(url)
                self.external_links.update(external_links)
                
                # Add new internal links to processing queue
                for link in internal_links:
                    if (link not in self.scraped_urls and 
                        link not in self.processing and 
                        link not in self.to_scrape):
                        self.to_scrape.append(link)
                
                self.processing.remove(url)
                
        except Exception as e:
            logging.error(f"Error processing batch with {url}: {e}")
            with self.lock:
                self.processing.remove(url)

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