import requests
import csv
import re
import hashlib
import time

prod_hashes = {0: True}

# Shared session so all requests reuse one connection pool.
session = requests.Session()

# Minimum spacing between request starts. The JSON endpoint needs only one
# request per 250 products (instead of one per product), so the whole store
# is a handful of requests -- generous spacing costs almost nothing and
# store.marxist.ca's rate limiter trips easily on an IP it has throttled before.
_last_request_at = 0.0
MIN_REQUEST_INTERVAL = 3.0  # seconds between request starts

def throttle():
    global _last_request_at
    wait = _last_request_at + MIN_REQUEST_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()

# Reporting category for each collection URL handle. products.json does not
# carry the collection title, and the /collections/<handle>.json metadata
# endpoint is rate-limited far more aggressively than products.json (observed:
# every .json call 429s while every products.json call sails through), so the
# categories are kept here. Handles not listed fall back to the title-cased
# handle ("books" -> "Books").
HANDLE_CATEGORIES = {
    'in-defence-of-marxism': 'IDOM',
    'papers': 'Communist Revolution',
}

# 429 = rate limited; 502/503/504 = transient upstream/proxy trouble. All worth retrying.
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

def request_with_retry(url, headers, max_retries=10, base_delay=1.4):
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
#    pip install requests
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

def scrape_category(url, items_to_exclude, items_to_rename, max_items=4500):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}
    items = []
    page = 1
    first_CR_item = True

    handle = url.rstrip('/').rsplit('/', 1)[-1]
    category = HANDLE_CATEGORIES.get(handle, handle.replace('-', ' ').title())

    # Shopify serves up to 250 products per products.json page, so a whole
    # collection is one or two requests instead of one request per product.
    while len(items) < max_items:
        response = request_with_retry(f"{url}/products.json?limit=250&page={page}", headers)

        if response.status_code != 200:
            print(f"Failed to retrieve data from {url}/products.json?page={page}: {response.status_code}")
            break

        products = response.json().get('products', [])

        if not products:
            break  # Exit if no products found on the page

        for product in products[:max_items - len(items)]:
            title = re.sub(r"[\",]", "", product['title']).strip()
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

            # Price of the first available variant -- the one the product page
            # displays as the regular price (e.g. the Paper variant for IDOM
            # issues, or the Digital variant when Print is sold out). Falls
            # back to the first variant when none are available.
            try:
                variants = product['variants']
                variant = next((v for v in variants if v.get('available')), variants[0])
                price = float(variant['price'])
            except (IndexError, KeyError, TypeError, ValueError):
                print(f"error parsing price for title {title} variants: ({product.get('variants')})")
                price = None

            items.append({'Item Name': item_name, 'Description': title, 'Reporting Category': category, 'Price': price, 'SKU': title_hash_str, 'Sellable': 'Y', 'Variation Name': ' ', 'Item Type': 'Physical'})

        page += 1

    return items

def scrape_marxist_store(categories, output_file, max_items_per_category=3500):
    all_items = []

    # product codes (hashes) to be excluded
    items_to_exclude = [row['Title'] for row in read_csv_rows("items_to_exclude.csv")]

    # item names to be renamed
    items_to_rename = {row['title']: row['rename_to'] for row in read_csv_rows("items_to_rename.csv")}

    # website scraping
    for category_url in categories:
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
    # Collection URLs (the scraper reads each collection's products.json).
    category_urls = [
        "https://store.marxist.ca/collections/in-defence-of-marxism",
        "https://store.marxist.ca/collections/books",
        "https://store.marxist.ca/collections/booklets",
        "https://store.marxist.ca/collections/papers"
    ]
    output_csv = "marxist_store_items.csv"
    scrape_marxist_store(category_urls, output_csv)
