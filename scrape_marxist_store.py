import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
import csv
import re
import hashlib
import time
import threading
from concurrent.futures import ThreadPoolExecutor

prod_hashes = {0: True}

# The bigger this is, the more product-detail requests are in flight at once.
# Kept low (rather than something like 6) because store.marxist.ca's Shopify
# rate limiter appears to key off concurrent/burst requests, not just average
# throughput -- raise it only after a full run completes with zero 429s.
PARALLELIZATION_THRESHOLD = 3

# Shared session so all requests reuse a connection pool sized to the
# concurrency threshold above, instead of each thread opening its own.
session = requests.Session()
_adapter = HTTPAdapter(pool_connections=PARALLELIZATION_THRESHOLD, pool_maxsize=PARALLELIZATION_THRESHOLD)
session.mount('https://', _adapter)
session.mount('http://', _adapter)

# Global request pacing, shared by every thread -- decoupled from
# PARALLELIZATION_THRESHOLD, which only limits how many requests may be in
# flight at once. This limits how often a request may actually be sent.
_rate_lock = threading.Lock()
_last_request_at = 0.0
MIN_REQUEST_INTERVAL = 0.5  # seconds between request starts, across all threads

def throttle():
    global _last_request_at
    with _rate_lock:
        wait = _last_request_at + MIN_REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()

# Shopify collection titles that should be reported under a different
# category name in the output CSV.
CATEGORY_RENAMES = {'In Defence of Marxism': 'IDOM'}

# 429 = rate limited; 502/503/504 = transient upstream/proxy trouble. All worth retrying.
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

def request_with_retry(url, headers, max_retries=8, base_delay=1.0):
    for attempt in range(max_retries + 1):
        throttle()
        resp = session.get(url, headers=headers)
        if resp.status_code not in RETRYABLE_STATUS_CODES:
            return resp
        if attempt == max_retries:
            break  # out of retries -- caller logs/skips as before
        retry_after = resp.headers.get('Retry-After')
        delay = float(retry_after) if retry_after else base_delay * (2 ** attempt)
        print(f"{resp.status_code} from {url}, backing off {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
        time.sleep(delay)
    return resp

# Instructions to set up the project:
# 1. Open Visual Studio Code (VSCode).
# 2. Create a new folder for the project and open it in VSCode.
# 3. Open the terminal in VSCode (View -> Terminal).
# 4. Create a virtual environment by running:
#    python -m venv venv
# 5. Activate the virtual environment:
#    - On Windows: venv\Scripts\activate
#    - On macOS/Linux: source venv/bin/activate
# 6. Install required libraries:
#    pip install requests beautifulsoup4
# 7. Run the script using:
#    python scrape_marxist_store.py

def truncate_with_ellipsis(s, max_length):
    if len(s) <= max_length:
        return s  # No need to truncate if the string is already short enough
    if max_length <= 3:
        return s
    part_length = (max_length - 3) // 2
    return s[:part_length] + '...' + s[-part_length:]

def read_csv_rows(path):
    with open(path, newline='', encoding='utf-8') as file:
        return list(csv.DictReader(file))

def fetch_product_detail(url, prod_details, headers):
    return prod_details, request_with_retry(f"{url}{prod_details}", headers)

def scrape_category(url, items_to_exclude, items_to_rename, max_items=4500):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}
    items = []
    page = 1
    first_CR_item = True

    with ThreadPoolExecutor(max_workers=PARALLELIZATION_THRESHOLD) as executor:
        while len(items) < max_items:
            response = request_with_retry(f"{url}?page={page}", headers)

            if response.status_code != 200:
                print(f"Failed to retrieve data from {url}?page={page}: {response.status_code}")
                break

            soup = BeautifulSoup(response.content, 'html.parser')
            main_page = False
            cat_obj = soup.find('h1', class_='collection-hero__title')
            if cat_obj is not None:
                category = cat_obj.get_text(strip=True)
            else:
                main_page = True
                category = soup.find('h2', class_='title').get_text(strip=True)
            category = category.replace('Collection:','')
            category = CATEGORY_RENAMES.get(category, category)
            products = soup.find_all('li', class_='grid__item')

            if not products:
                break  # Exit if no products found on the page

            hrefs = [product.find('a')['href'] for product in products][:max_items - len(items)]
            fetch = lambda href: fetch_product_detail(url, href, headers)

            for prod_details, prod_details_resp in executor.map(fetch, hrefs):
                if prod_details_resp.status_code != 200:
                    print(f"Failed to retrieve product details from {url}{prod_details}: {prod_details_resp.status_code}")
                    continue

                prod_details_soup = BeautifulSoup(prod_details_resp.content, 'html.parser')
                price_text = prod_details_soup.find('span', class_='price-item--regular').get_text(strip=True)
                title = prod_details_soup.find('h1').get_text(strip=True)
                title = re.sub(r"[\",]", "", title)
                candidate_item_name = title
                for mustmatchtitle, replaceto in items_to_rename.items():
                    if mustmatchtitle in title:
                        candidate_item_name = title.replace(mustmatchtitle, replaceto, 1)
                item_name = truncate_with_ellipsis(candidate_item_name, 40)

                # add a ** before the first CR item to make sure it appears at the top of the list when sorted alphabetically by item name
                if category == "Communist Revolution" and first_CR_item:
                    item_name = "** " + item_name
                    first_CR_item = False

                title_hash = int(hashlib.sha256(title.encode('utf-8')).hexdigest(), 16)
                # avoid duplicates
                if title_hash in prod_hashes:
                    continue
                prod_hashes[title_hash] = True
                title_hash = title_hash % 10000000
                title_hash_str = "M" + str(title_hash)

                if title in items_to_exclude: # exact match
                    print(f"Excluding item with title {title} and SKU {title_hash_str}")
                    continue

                # Parse price to float
                try:
                    price = float(re.sub(r"[^0-9.]", "", price_text))
                except ValueError:
                    print(f"error parsing price for title {title} price_text: ({price_text})")
                    price = None

                items.append({'Item Name': item_name, 'Description': title, 'Reporting Category': category, 'Price': price, 'SKU': title_hash_str, 'Sellable': 'Y', 'Variation Name': ' ', 'Item Type': 'Physical'})

            if main_page:
                break

            page += 1

    return items

def scrape_marxist_store(categories, output_file, max_items_per_category=3500):
    all_items = []

    # product codes (hashes) to be excluded
    items_to_exclude = [row['Title'] for row in read_csv_rows("items_to_exclude.csv")]

    # item names to be renamed
    items_to_rename = {row['title']: row['rename_to'] for row in read_csv_rows("items_to_rename.csv")}

    # website scraping
    for i, category_url in enumerate(categories):
        if i > 0:
            time.sleep(5)  # cooldown so this category doesn't inherit the previous one's rate-limit window
        print(f"Scraping category: {category_url}")
        items = scrape_category(category_url, items_to_exclude, items_to_rename, max_items=max_items_per_category)
        all_items.extend(items)


    # Write to CSV
    with open(output_file, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=['Item Name', 'Description', 'Reporting Category', 'Price', 'SKU', 'Sellable','Variation Name', 'Item Type'])
        writer.writeheader()
        writer.writerows(all_items)

    print(f"Scraped {len(all_items)} items across all categories and saved to {output_file}.")

if __name__ == "__main__":
    category_urls = [
        "https://store.marxist.ca/collections/in-defence-of-marxism",
        "https://store.marxist.ca/collections/books",
        "https://store.marxist.ca/collections/booklets",
        "https://store.marxist.ca/collections/papers"
        # "https://store.marxist.ca/"
    ]
    output_csv = "marxist_store_items.csv"
    scrape_marxist_store(category_urls, output_csv)
