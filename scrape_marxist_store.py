import requests
from bs4 import BeautifulSoup
import csv
import re
import hashlib

prod_hashes = {0: True}

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

def scrape_category(url, max_items=3500):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}
    items = []
    page = 1

    while len(items) < max_items:
        response = requests.get(f"{url}?page={page}", headers=headers)

        if response.status_code != 200:
            print(f"Failed to retrieve data from {url}?page={page}: {response.status_code}")
            break

        soup = BeautifulSoup(response.content, 'html.parser')
        main_page = False
        cat_obj = soup.find('h1', class_='collection-hero__title')
        if cat_obj is not None:
            category = soup.find('h1', class_='collection-hero__title').get_text(strip=True)
        else:
            main_page = True
            category = soup.find('h2', class_='title').get_text(strip=True)
        category = category.replace('Collection:','')
        products = soup.find_all('li', class_='grid__item')

        if not products:
            break  # Exit if no products found on the page

        for product in products:
            if len(items) >= max_items:
                break
            a_elem = product.find('a')
            prod_details = a_elem['href']
            prod_details_resp = requests.get(f"{url}{prod_details}", headers=headers)

            if prod_details_resp.status_code != 200:
                print(f"Failed to retrieve product details from {url}{prod_details}: {prod_details_resp.status_code}")
                break

            prod_details_soup = BeautifulSoup(prod_details_resp.content, 'html.parser')
            price_text = prod_details_soup.find('span', class_='price-item--regular').get_text(strip=True)
            title = prod_details_soup.find('h1').get_text(strip=True)
            title = re.sub(r"[\",]", "", title)
            item_name = truncate_with_ellipsis(title, 36)
            title_hash = int(hashlib.sha256(title.encode('utf-8')).hexdigest(), 16)
            # avoid duplicates
            if title_hash in prod_hashes:
                continue
            else:
                prod_hashes[title_hash] = True
            title_hash = title_hash % 10000000
            title_hash_str = "M" + str(title_hash)
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

    # IDOM
    extra_idom_csv = "idom_items.csv"
    with open(extra_idom_csv, mode='r', newline='', encoding='utf-8') as file:
        reader = csv.DictReader(file, fieldnames=['Item Name', 'Description', 'Price'])
        header = True
        items = []
        for row in reader:
            if header:
                header = False
                continue
            item_name = truncate_with_ellipsis(row['Item Name'], 36)
            title_hash = int(hashlib.sha256(row['Item Name'].encode('utf-8')).hexdigest(), 16)
            # avoid duplicates
            if title_hash in prod_hashes:
                continue
            else:
                prod_hashes[title_hash] = True
            title_hash = title_hash % 10000000
            title_hash_str = "M" + str(title_hash)
            # Parse price to float
            price = float(re.sub(r"[^0-9.]", "", row['Price']))
            items.append({'Item Name': row['Item Name'], 'Description': row['Description'], 'Reporting Category': 'IDOM', 'Price': price, 'SKU': title_hash_str, 'Sellable': 'Y', 'Variation Name': ' ', 'Item Type': 'Physical'})
        all_items.extend(items)

    # website scraping
    for category_url in categories:
        print(f"Scraping category: {category_url}")
        items = scrape_category(category_url, max_items=max_items_per_category)
        all_items.extend(items)


    # Write to CSV
    with open(output_file, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=['Item Name', 'Description', 'Reporting Category', 'Price', 'SKU', 'Sellable','Variation Name', 'Item Type'])
        writer.writeheader()
        writer.writerows(all_items)

    print(f"Scraped {len(all_items)} items across all categories and saved to {output_file}.")

if __name__ == "__main__":
    category_urls = [
        "https://store.marxist.ca/collections/books",
        "https://store.marxist.ca/collections/booklets",
        "https://store.marxist.ca/collections/papers"
        # "https://store.marxist.ca/"
    ]
    output_csv = "marxist_store_items.csv"
    scrape_marxist_store(category_urls, output_csv)
